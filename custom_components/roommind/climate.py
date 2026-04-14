"""Climate platform for RoomMind."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL_ONLY,
    CLIMATE_MODE_HEAT_ONLY,
    DEFAULT_COMFORT_COOL,
    DEFAULT_COMFORT_HEAT,
    DEFAULT_COMFORT_TEMP,
    DOMAIN,
    MODE_COOLING,
    MODE_HEATING,
    OVERRIDE_CUSTOM,
)
from .control.mpc_controller import check_acs_can_heat
from .coordinator import RoomMindCoordinator
from .utils.device_utils import get_ac_eids, get_trv_eids
from .utils.entity_naming import get_area_name
from .utils.schedule_utils import get_active_schedule_entity


def _create_room_climates(
    coordinator: RoomMindCoordinator,
    area_id: str,
) -> list[ClimateEntity]:
    """Create climate entities for a room."""
    return [RoomMindRoomClimate(coordinator, area_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RoomMind climate entities from a config entry."""
    coordinator: RoomMindCoordinator = hass.data[DOMAIN][entry.entry_id]
    store = hass.data[DOMAIN]["store"]
    coordinator.async_add_climate_entities = async_add_entities
    rooms = store.get_rooms()
    entities: list[ClimateEntity] = []
    for area_id in rooms:
        entities.extend(_create_room_climates(coordinator, area_id))
        coordinator._climate_entity_areas.add(area_id)
    if entities:
        async_add_entities(entities)


class _RoomMindBaseClimate(CoordinatorEntity, ClimateEntity):
    """Shared room climate helpers."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0

    def __init__(self, coordinator: RoomMindCoordinator, area_id: str) -> None:
        super().__init__(coordinator)
        self._area_id = area_id

    def _get_room(self) -> dict | None:
        """Return the stored room configuration."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        return store.get_room(self._area_id)

    def _get_live_room(self) -> dict | None:
        """Return live coordinator data for the room."""
        data = self.coordinator.data or {}
        return data.get("rooms", {}).get(self._area_id)

    def _override_targets(self, room: dict | None = None) -> tuple[float | None, float | None]:
        """Return override heat/cool values for the room."""
        room = room or self._get_room() or {}
        override_heat = room.get("override_heat_temp")
        override_cool = room.get("override_cool_temp")
        heat = float(override_heat) if isinstance(override_heat, (int, float)) else None
        cool = float(override_cool) if isinstance(override_cool, (int, float)) else None
        if heat is None and cool is None:
            override_temp = room.get("override_temp")
            if isinstance(override_temp, (int, float)):
                value = float(override_temp)
                return value, value
        return heat, cool

    def _is_override_active(self) -> bool:
        """Return True if override is currently active."""
        room = self._get_room()
        if not room:
            return False
        override_temp = room.get("override_temp")
        override_heat = room.get("override_heat_temp")
        override_cool = room.get("override_cool_temp")
        if override_temp is None and override_heat is None and override_cool is None:
            return False
        override_until = room.get("override_until")
        return override_until is None or time.time() < override_until


class RoomMindRoomClimate(_RoomMindBaseClimate):
    """Room climate entity exposing the effective RoomMind targets."""

    _attr_icon = "mdi:thermostat"

    def __init__(self, coordinator: RoomMindCoordinator, area_id: str) -> None:
        super().__init__(coordinator, area_id)
        self._attr_unique_id = f"{DOMAIN}_{area_id}_climate"
        self._attr_name = f"{get_area_name(coordinator.hass, area_id)} Climate"
        self.entity_id = f"climate.{DOMAIN}_{area_id}"

    def _room_capabilities(self) -> tuple[bool, bool]:
        """Return whether the room can heat and/or cool."""
        room = self._get_room() or {}
        devices = room.get("devices", [])
        has_heat = bool(get_trv_eids(devices) or room.get("thermostats", []))
        has_cool = bool(get_ac_eids(devices) or room.get("acs", []))
        if devices and check_acs_can_heat(self.hass, room):
            has_heat = True

        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_HEAT_ONLY:
            has_cool = False
        elif climate_mode == CLIMATE_MODE_COOL_ONLY:
            has_heat = False
        return has_heat, has_cool

    def _supports_target_range(self) -> bool:
        """Return True when the room should expose heat/cool targets."""
        has_heat, has_cool = self._room_capabilities()
        return has_heat and has_cool

    def _resolved_targets_fallback(self) -> tuple[float | None, float | None]:
        """Resolve current room targets from config when live data is absent."""
        room = self._get_room()
        if not room:
            return None, None
        store = self.coordinator.hass.data[DOMAIN]["store"]
        settings = store.get_settings()
        schedule_entity_id = get_active_schedule_entity(self.hass, room)
        targets = self.coordinator._resolve_target_temps(  # noqa: SLF001 - shared integration helper
            room,
            settings,
            schedule_blocks=None,
            schedule_entity_id=schedule_entity_id,
        )
        return targets.heat, targets.cool

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return supported HVAC modes for this room."""
        has_heat, has_cool = self._room_capabilities()
        modes: list[HVACMode] = [HVACMode.OFF]
        if has_heat and has_cool:
            modes.extend([HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL])
        elif has_heat:
            modes.append(HVACMode.HEAT)
        elif has_cool:
            modes.append(HVACMode.COOL)
        return modes

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported features for this room climate."""
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._supports_target_range():
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the configured room HVAC mode."""
        room = self._get_room()
        if not room or not room.get("climate_control_enabled", True):
            return HVACMode.OFF

        has_heat, has_cool = self._room_capabilities()
        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_HEAT_ONLY and has_heat:
            return HVACMode.HEAT
        if climate_mode == CLIMATE_MODE_COOL_ONLY and has_cool:
            return HVACMode.COOL
        if has_heat and has_cool:
            return HVACMode.HEAT_COOL
        if has_heat:
            return HVACMode.HEAT
        if has_cool:
            return HVACMode.COOL
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        """Return the room's current live HVAC action."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        room_data = self._get_live_room()
        if room_data:
            mode = room_data.get("mode")
            if mode == MODE_HEATING:
                return HVACAction.HEATING
            if mode == MODE_COOLING:
                return HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Return the room's current temperature from coordinator data."""
        room_data = self._get_live_room()
        if not room_data:
            return None
        val = room_data.get("current_temp")
        return float(val) if isinstance(val, (int, float)) else None

    @property
    def target_temperature(self) -> float:
        """Return the room's effective single-point target."""
        room_data = self._get_live_room()
        if room_data:
            val = room_data.get("target_temp")
            if isinstance(val, (int, float)):
                return float(val)

        heat_target, cool_target = self._override_targets()
        if heat_target is not None:
            return heat_target
        if cool_target is not None:
            return cool_target

        resolved_heat, resolved_cool = self._resolved_targets_fallback()
        if self.hvac_mode == HVACMode.COOL and resolved_cool is not None:
            return resolved_cool
        if resolved_heat is not None:
            return resolved_heat
        if resolved_cool is not None:
            return resolved_cool
        return DEFAULT_COMFORT_TEMP

    @property
    def target_temperature_low(self) -> float | None:
        """Return the effective heating target for heat/cool rooms."""
        if not self._supports_target_range():
            return None
        room_data = self._get_live_room()
        if room_data:
            val = room_data.get("heat_target")
            if isinstance(val, (int, float)):
                return float(val)
        heat_target, _ = self._override_targets()
        if heat_target is not None:
            return heat_target
        resolved_heat, _ = self._resolved_targets_fallback()
        return resolved_heat if resolved_heat is not None else DEFAULT_COMFORT_HEAT

    @property
    def target_temperature_high(self) -> float | None:
        """Return the effective cooling target for heat/cool rooms."""
        if not self._supports_target_range():
            return None
        room_data = self._get_live_room()
        if room_data:
            val = room_data.get("cool_target")
            if isinstance(val, (int, float)):
                return float(val)
        _, cool_target = self._override_targets()
        if cool_target is not None:
            return cool_target
        _, resolved_cool = self._resolved_targets_fallback()
        return resolved_cool if resolved_cool is not None else DEFAULT_COMFORT_COOL

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a custom room target override."""
        store = self.coordinator.hass.data[DOMAIN]["store"]

        target_temp_low = kwargs.get("target_temp_low")
        target_temp_high = kwargs.get("target_temp_high")
        if target_temp_low is not None or target_temp_high is not None:
            low = float(target_temp_low) if target_temp_low is not None else self.target_temperature_low
            high = float(target_temp_high) if target_temp_high is not None else self.target_temperature_high
            if low is None or high is None:
                return
            if low > high:
                low, high = high, low
            await store.async_update_room(
                self._area_id,
                {
                    "override_temp": None,
                    "override_heat_temp": low,
                    "override_cool_temp": high,
                    "override_until": None,
                    "override_type": OVERRIDE_CUSTOM,
                },
            )
            await self.coordinator.async_request_refresh()
            return

        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        await store.async_update_room(
            self._area_id,
            {
                "override_temp": temperature,
                "override_heat_temp": None,
                "override_cool_temp": None,
                "override_until": None,
                "override_type": OVERRIDE_CUSTOM,
            },
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Update the room's configured climate mode."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        if hvac_mode == HVACMode.OFF:
            await store.async_update_room(self._area_id, {"climate_control_enabled": False})
        elif hvac_mode == HVACMode.HEAT:
            await store.async_update_room(
                self._area_id,
                {
                    "climate_control_enabled": True,
                    "climate_mode": CLIMATE_MODE_HEAT_ONLY,
                },
            )
        elif hvac_mode == HVACMode.COOL:
            await store.async_update_room(
                self._area_id,
                {
                    "climate_control_enabled": True,
                    "climate_mode": CLIMATE_MODE_COOL_ONLY,
                },
            )
        elif hvac_mode == HVACMode.HEAT_COOL:
            await store.async_update_room(
                self._area_id,
                {
                    "climate_control_enabled": True,
                    "climate_mode": CLIMATE_MODE_AUTO,
                },
            )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn room climate control on."""
        del kwargs
        if self.hvac_mode != HVACMode.OFF:
            return
        available_modes = [mode for mode in self.hvac_modes if mode != HVACMode.OFF]
        if not available_modes:
            return
        preferred_mode = HVACMode.HEAT_COOL if HVACMode.HEAT_COOL in available_modes else available_modes[0]
        await self.async_set_hvac_mode(preferred_mode)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn room climate control off."""
        del kwargs
        await self.async_set_hvac_mode(HVACMode.OFF)
