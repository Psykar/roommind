"""Climate platform for RoomMind."""

from __future__ import annotations

import math
import time
from typing import Any

from homeassistant.components.climate import (
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
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
    DEFAULT_ECO_COOL,
    DEFAULT_ECO_HEAT,
    DOMAIN,
    MODE_COOLING,
    MODE_HEATING,
    OVERRIDE_BOOST,
    OVERRIDE_CUSTOM,
    OVERRIDE_ECO,
)
from .coordinator import RoomMindCoordinator
from .utils.device_utils import get_ac_eids, get_trv_eids


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


class RoomMindRoomClimate(CoordinatorEntity, ClimateEntity):
    """Unified climate entity for a RoomMind room."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:thermostat"
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [PRESET_ECO, PRESET_COMFORT, PRESET_BOOST]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0

    def __init__(self, coordinator: RoomMindCoordinator, area_id: str) -> None:
        super().__init__(coordinator)
        self._area_id = area_id
        self._attr_unique_id = f"{DOMAIN}_{area_id}_climate"
        self._attr_name = area_id
        self.entity_id = f"climate.{DOMAIN}_{area_id}"

    def _get_room(self) -> dict | None:
        """Return the stored room configuration."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        return store.get_room(self._area_id)

    def _get_live_room(self) -> dict | None:
        """Return live coordinator data for the room."""
        data = self.coordinator.data or {}
        return data.get("rooms", {}).get(self._area_id)

    def _room_capabilities(self) -> tuple[bool, bool]:
        """Return whether the room can heat and/or cool."""
        room = self._get_room() or {}
        has_heat = bool(get_trv_eids(room.get("devices", [])))
        has_cool = bool(get_ac_eids(room.get("devices", [])))
        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_HEAT_ONLY:
            has_cool = False
        elif climate_mode == CLIMATE_MODE_COOL_ONLY:
            has_heat = False
        return has_heat, has_cool

    def _comfort_target(self, room: dict | None = None) -> float:
        """Return the room's comfort target for the current climate mode."""
        room = room or self._get_room() or {}
        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_COOL_ONLY:
            return float(room.get("comfort_cool", DEFAULT_COMFORT_COOL))
        return float(room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT)))

    def _eco_target(self, room: dict | None = None) -> float:
        """Return the room's eco target for the current climate mode."""
        room = room or self._get_room() or {}
        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_COOL_ONLY:
            return float(room.get("eco_cool", DEFAULT_ECO_COOL))
        return float(room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT)))

    def _is_override_active(self) -> bool:
        """Return True if override is currently active."""
        room = self._get_room()
        if not room:
            return False
        override_temp = room.get("override_temp")
        if override_temp is None:
            return False
        override_until = room.get("override_until")
        return override_until is None or time.time() < override_until

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
    def hvac_mode(self) -> HVACMode:
        """Return the room's effective HVAC mode."""
        room = self._get_room()
        if not room:
            return HVACMode.OFF
        if not room.get("climate_control_enabled", True):
            return HVACMode.OFF

        has_heat, has_cool = self._room_capabilities()
        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if (climate_mode == CLIMATE_MODE_HEAT_ONLY and has_heat) or (
            climate_mode == CLIMATE_MODE_AUTO and has_heat and not has_cool
        ):
            return HVACMode.HEAT
        if (climate_mode == CLIMATE_MODE_COOL_ONLY and has_cool) or (
            climate_mode == CLIMATE_MODE_AUTO and has_cool and not has_heat
        ):
            return HVACMode.COOL
        if has_heat and has_cool:
            return HVACMode.HEAT_COOL
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
    def target_temperature(self) -> float:
        """Return the room's effective target temperature."""
        room_data = self._get_live_room()
        if room_data:
            val = room_data.get("target_temp")
            if isinstance(val, (int, float)):
                return float(val)
        room = self._get_room()
        if room and self._is_override_active():
            val = room.get("override_temp")
            if isinstance(val, (int, float)):
                return float(val)
        return DEFAULT_COMFORT_TEMP

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset, if any."""
        room = self._get_room()
        if not room or not self._is_override_active():
            return None
        override_type = room.get("override_type")
        if override_type == OVERRIDE_ECO:
            return PRESET_ECO
        if override_type == OVERRIDE_BOOST:
            return PRESET_BOOST
        override_temp = room.get("override_temp")
        if isinstance(override_temp, (int, float)) and math.isclose(
            float(override_temp),
            self._comfort_target(room),
            abs_tol=0.05,
        ):
            return PRESET_COMFORT
        return None

    @property
    def current_temperature(self) -> float | None:
        """Return the room's current temperature from coordinator data."""
        room_data = self._get_live_room()
        if not room_data:
            return None
        val = room_data.get("current_temp")
        return float(val) if isinstance(val, (int, float)) else None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a custom room target temperature override."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        store = self.coordinator.hass.data[DOMAIN]["store"]
        await store.async_update_room(
            self._area_id,
            {
                "override_temp": temperature,
                "override_until": None,
                "override_type": OVERRIDE_CUSTOM,
            },
        )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the room HVAC mode via climate mode + enable/disable state."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        if hvac_mode == HVACMode.OFF:
            await store.async_update_room(self._area_id, {"climate_control_enabled": False})
        elif hvac_mode == HVACMode.HEAT:
            await store.async_update_room(
                self._area_id,
                {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_HEAT_ONLY},
            )
        elif hvac_mode == HVACMode.COOL:
            await store.async_update_room(
                self._area_id,
                {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_COOL_ONLY},
            )
        elif hvac_mode == HVACMode.HEAT_COOL:
            await store.async_update_room(
                self._area_id,
                {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_AUTO},
            )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Activate one of the room's built-in override presets."""
        room = self._get_room() or {}
        if preset_mode == PRESET_ECO:
            changes = {
                "override_temp": self._eco_target(room),
                "override_until": None,
                "override_type": OVERRIDE_ECO,
            }
        elif preset_mode == PRESET_COMFORT:
            changes = {
                "override_temp": self._comfort_target(room),
                "override_until": None,
                "override_type": OVERRIDE_CUSTOM,
            }
        elif preset_mode == PRESET_BOOST:
            changes = {
                "override_temp": self._comfort_target(room),
                "override_until": None,
                "override_type": OVERRIDE_BOOST,
            }
        else:
            return
        store = self.coordinator.hass.data[DOMAIN]["store"]
        await store.async_update_room(self._area_id, changes)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn room climate control on."""
        if self.hvac_mode != HVACMode.OFF:
            return
        preferred_mode = HVACMode.HEAT_COOL if HVACMode.HEAT_COOL in self.hvac_modes else self.hvac_modes[-1]
        await self.async_set_hvac_mode(preferred_mode)

    async def async_turn_off(self) -> None:
        """Turn room climate control off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
