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
    DEFAULT_ECO_COOL,
    DEFAULT_ECO_HEAT,
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

PRESET_COMFORT = "comfort"
PRESET_ECO = "eco"
PRESET_OVERRIDE = "override"
PRESET_SCHEDULE = "schedule"


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
        self._attr_translation_key = "room_climate"
        self.entity_id = f"climate.{DOMAIN}_{area_id}"
        self._pending_state: dict[str, Any] | None = None

    def _set_pending_state(self, **kwargs: Any) -> None:
        """Store optimistic state shown until the next coordinator refresh lands."""
        self._pending_state = {"requested_at": time.time(), **kwargs}
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()

    def _clear_pending_state(self) -> None:
        """Clear optimistic state after coordinator confirmation."""
        self._pending_state = None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose pending state so dashboards can show a processing indicator."""
        if not self._pending_state:
            return None
        return {
            "pending_update": True,
            "pending_since": self._pending_state["requested_at"],
        }

    @property
    def assumed_state(self) -> bool:
        """Return True while showing optimistic state."""
        return self._pending_state is not None

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when the coordinator publishes fresh data."""
        self._clear_pending_state()
        if getattr(self, "hass", None) is not None:
            super()._handle_coordinator_update()

    def _room_capabilities(self, room: dict | None = None) -> tuple[bool, bool]:
        """Return whether the room can heat and/or cool."""
        room = room or self._get_room() or {}
        devices = room.get("devices", [])
        has_heat = bool(get_trv_eids(devices) or room.get("thermostats", []))
        has_cool = bool(get_ac_eids(devices) or room.get("acs", []))
        if devices and check_acs_can_heat(self.coordinator.hass, room):
            has_heat = True

        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_HEAT_ONLY:
            has_cool = False
        elif climate_mode == CLIMATE_MODE_COOL_ONLY:
            has_heat = False
        return has_heat, has_cool

    def _supports_target_range(self, room: dict | None = None) -> bool:
        """Return True when the room should expose heat/cool targets."""
        has_heat, has_cool = self._room_capabilities(room)
        return has_heat and has_cool

    def _has_schedule(self, room: dict | None = None) -> bool:
        """Return True when the room has at least one schedule configured."""
        room = room or self._get_room() or {}
        return bool(room.get("schedules"))

    def _preset_mode_from_room(self, room: dict | None = None) -> str | None:
        """Map the current RoomMind state to a Home Assistant preset mode."""
        room = room or self._get_room()
        if not room:
            return None
        if self._is_override_active():
            override_type = room.get("override_type")
            if override_type == "boost":
                return PRESET_COMFORT
            if override_type == "eco":
                return PRESET_ECO
            if override_type == OVERRIDE_CUSTOM:
                return PRESET_OVERRIDE
        if self._has_schedule(room):
            return PRESET_SCHEDULE
        return None

    def _custom_override_payload(
        self,
        *,
        temperature: float | None = None,
        low: float | None = None,
        high: float | None = None,
    ) -> dict[str, float | None | str]:
        """Build the store payload for a custom override."""
        if low is not None or high is not None:
            if low is None or high is None:
                raise ValueError("Both low and high targets are required for a range override")
            if low > high:
                low, high = high, low
            return {
                "override_temp": None,
                "override_heat_temp": low,
                "override_cool_temp": high,
                "override_until": None,
                "override_type": OVERRIDE_CUSTOM,
            }
        if temperature is None:
            raise ValueError("Temperature is required for a single-point override")
        return {
            "override_temp": temperature,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        }

    def _preset_override_payload(self, preset_mode: str, room: dict | None = None) -> dict[str, float | None | str]:
        """Build the store payload for a preset selection."""
        room = room or self._get_room() or {}
        if preset_mode == PRESET_SCHEDULE:
            return {
                "override_temp": None,
                "override_heat_temp": None,
                "override_cool_temp": None,
                "override_until": None,
                "override_type": None,
            }

        if preset_mode == PRESET_OVERRIDE:
            if self._supports_target_range(room):
                low = self.target_temperature_low
                high = self.target_temperature_high
                if low is None or high is None:
                    raise ValueError("Range targets are unavailable for override preset")
                return self._custom_override_payload(low=low, high=high)
            return self._custom_override_payload(temperature=self.target_temperature)

        if preset_mode == PRESET_COMFORT:
            heat_target = room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT))
            cool_target = room.get("comfort_cool", DEFAULT_COMFORT_COOL)
            override_type = "boost"
        elif preset_mode == PRESET_ECO:
            heat_target = room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT))
            cool_target = room.get("eco_cool", DEFAULT_ECO_COOL)
            override_type = "eco"
        else:
            raise ValueError(f"Unsupported preset mode: {preset_mode}")

        if self._supports_target_range(room):
            return {
                "override_temp": None,
                "override_heat_temp": float(heat_target),
                "override_cool_temp": float(cool_target),
                "override_until": None,
                "override_type": override_type,
            }

        has_heat, has_cool = self._room_capabilities(room)
        target = cool_target if has_cool and not has_heat else heat_target
        return {
            "override_temp": float(target),
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": override_type,
        }

    def _resolved_targets_fallback(self) -> tuple[float | None, float | None]:
        """Resolve current room targets from config when live data is absent."""
        room = self._get_room()
        if not room:
            return None, None
        store = self.coordinator.hass.data[DOMAIN]["store"]
        settings = store.get_settings()
        schedule_entity_id = get_active_schedule_entity(self.coordinator.hass, room)
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
        features = (
            ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.PRESET_MODE
        )
        if self._supports_target_range():
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def preset_modes(self) -> list[str]:
        """Return supported preset modes for this room climate."""
        modes = [PRESET_COMFORT, PRESET_ECO, PRESET_OVERRIDE]
        if self._has_schedule():
            modes.append(PRESET_SCHEDULE)
        return modes

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if self._pending_state and "preset_mode" in self._pending_state:
            return self._pending_state["preset_mode"]
        return self._preset_mode_from_room()

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the configured room HVAC mode."""
        if self._pending_state and "hvac_mode" in self._pending_state:
            return self._pending_state["hvac_mode"]
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
        if self._pending_state and "target_temperature" in self._pending_state:
            return self._pending_state["target_temperature"]
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
        if self._pending_state and "target_temperature_low" in self._pending_state:
            return self._pending_state["target_temperature_low"]
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
        if self._pending_state and "target_temperature_high" in self._pending_state:
            return self._pending_state["target_temperature_high"]
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
        active_preset = self.preset_mode

        target_temp_low = kwargs.get("target_temp_low")
        target_temp_high = kwargs.get("target_temp_high")
        if target_temp_low is not None or target_temp_high is not None:
            low = float(target_temp_low) if target_temp_low is not None else self.target_temperature_low
            high = float(target_temp_high) if target_temp_high is not None else self.target_temperature_high
            if low is None or high is None:
                return
            if active_preset == PRESET_COMFORT:
                room = await store.async_save_room(
                    self._area_id,
                    {"comfort_heat": low, "comfort_cool": high},
                )
                await store.async_update_room(self._area_id, self._preset_override_payload(PRESET_COMFORT, room))
                self._set_pending_state(
                    preset_mode=PRESET_COMFORT,
                    target_temperature_low=low,
                    target_temperature_high=high,
                )
            elif active_preset == PRESET_ECO:
                room = await store.async_save_room(
                    self._area_id,
                    {"eco_heat": low, "eco_cool": high},
                )
                await store.async_update_room(self._area_id, self._preset_override_payload(PRESET_ECO, room))
                self._set_pending_state(
                    preset_mode=PRESET_ECO,
                    target_temperature_low=low,
                    target_temperature_high=high,
                )
            else:
                await store.async_update_room(
                    self._area_id,
                    self._custom_override_payload(low=low, high=high),
                )
                self._set_pending_state(
                    preset_mode=PRESET_OVERRIDE,
                    target_temperature_low=low,
                    target_temperature_high=high,
                )
            await self.coordinator.async_request_refresh()
            return

        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        if active_preset == PRESET_COMFORT:
            room = self._get_room() or {}
            has_heat, has_cool = self._room_capabilities(room)
            changes = {"comfort_cool": temperature} if has_cool and not has_heat else {"comfort_heat": temperature}
            room = await store.async_save_room(self._area_id, changes)
            await store.async_update_room(self._area_id, self._preset_override_payload(PRESET_COMFORT, room))
            self._set_pending_state(
                preset_mode=PRESET_COMFORT,
                target_temperature=float(temperature),
            )
        elif active_preset == PRESET_ECO:
            room = self._get_room() or {}
            has_heat, has_cool = self._room_capabilities(room)
            changes = {"eco_cool": temperature} if has_cool and not has_heat else {"eco_heat": temperature}
            room = await store.async_save_room(self._area_id, changes)
            await store.async_update_room(self._area_id, self._preset_override_payload(PRESET_ECO, room))
            self._set_pending_state(
                preset_mode=PRESET_ECO,
                target_temperature=float(temperature),
            )
        else:
            await store.async_update_room(
                self._area_id,
                self._custom_override_payload(temperature=float(temperature)),
            )
            self._set_pending_state(
                preset_mode=PRESET_OVERRIDE,
                target_temperature=float(temperature),
            )
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the active climate preset mode."""
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Unsupported preset mode: {preset_mode}")
        store = self.coordinator.hass.data[DOMAIN]["store"]
        await store.async_update_room(self._area_id, self._preset_override_payload(preset_mode))
        pending: dict[str, Any] = {"preset_mode": preset_mode}
        if preset_mode == PRESET_COMFORT:
            room = self._get_room() or {}
            if self._supports_target_range(room):
                pending["target_temperature_low"] = room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT))
                pending["target_temperature_high"] = room.get("comfort_cool", DEFAULT_COMFORT_COOL)
            else:
                has_heat, has_cool = self._room_capabilities(room)
                pending["target_temperature"] = float(
                    room.get("comfort_cool", DEFAULT_COMFORT_COOL)
                    if has_cool and not has_heat
                    else room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT))
                )
        elif preset_mode == PRESET_ECO:
            room = self._get_room() or {}
            if self._supports_target_range(room):
                pending["target_temperature_low"] = room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT))
                pending["target_temperature_high"] = room.get("eco_cool", DEFAULT_ECO_COOL)
            else:
                has_heat, has_cool = self._room_capabilities(room)
                pending["target_temperature"] = float(
                    room.get("eco_cool", DEFAULT_ECO_COOL)
                    if has_cool and not has_heat
                    else room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT))
                )
        elif preset_mode == PRESET_OVERRIDE:
            if self._supports_target_range():
                pending["target_temperature_low"] = self.target_temperature_low
                pending["target_temperature_high"] = self.target_temperature_high
            else:
                pending["target_temperature"] = self.target_temperature
        self._set_pending_state(**pending)
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
        self._set_pending_state(hvac_mode=hvac_mode)
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
