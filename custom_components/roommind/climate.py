"""Climate platform for RoomMind."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL_ONLY,
    CLIMATE_MODE_HEAT_ONLY,
    CLIMATE_SOURCE_COMFORT_HOLD,
    CLIMATE_SOURCE_ECO_HOLD,
    CLIMATE_SOURCE_MANUAL_HOLD,
    CLIMATE_SOURCE_SCHEDULE,
    DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
    DEFAULT_COMFORT_COOL,
    DEFAULT_COMFORT_HEAT,
    DEFAULT_COMFORT_TEMP,
    DEFAULT_ECO_COOL,
    DEFAULT_ECO_HEAT,
    DOMAIN,
    MAX_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
    OVERRIDE_BOOST,
    OVERRIDE_CUSTOM,
    OVERRIDE_ECO,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_OVERRIDE,
    PRESET_SCHEDULE,
    SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES,
    TargetTemps,
    build_override_live,
    build_room_override_payload,
    clear_room_override_payload,
)
from .control.mpc_controller import check_acs_can_heat
from .coordinator import RoomMindCoordinator
from .utils.device_utils import get_ac_eids, get_trv_eids
from .utils.entity_naming import get_area_name

_PENDING_TIMEOUT_S = 120
_FLOAT_TOLERANCE = 0.05
_LOGGER = logging.getLogger(__name__)


def _create_room_climates(
    coordinator: RoomMindCoordinator,
    area_id: str,
) -> list[ClimateEntity]:
    """Create climate entities for a room."""
    return [RoomMindRoomClimate(coordinator, area_id), RoomMindOverrideClimate(coordinator, area_id)]


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

    def _get_live_room(self) -> dict[str, Any]:
        """Return live coordinator data for the room."""
        data = self.coordinator.data or {}
        return data.get("rooms", {}).get(self._area_id, {})

    def _fallback_capabilities(self) -> tuple[bool, bool]:
        """Return hardware heat/cool capability from room config."""
        room = self._get_room() or {}
        devices = room.get("devices", [])
        has_heat = bool(get_trv_eids(devices) or room.get("thermostats", []))
        has_cool = bool(get_ac_eids(devices) or room.get("acs", []))
        if devices and check_acs_can_heat(self.coordinator.hass, room):
            has_heat = True
        return has_heat, has_cool

    def _fallback_supported_hvac_modes(self) -> list[HVACMode]:
        """Return supported HVAC modes from room hardware when live data is absent."""
        has_heat, has_cool = self._fallback_capabilities()
        modes: list[HVACMode] = [HVACMode.OFF]
        if has_heat:
            modes.append(HVACMode.HEAT)
        if has_cool:
            modes.append(HVACMode.COOL)
        if has_heat and has_cool:
            modes.append(HVACMode.HEAT_COOL)
        return modes

    def _fallback_hvac_mode(self) -> HVACMode:
        """Return the configured HVAC mode from room config."""
        room = self._get_room() or {}
        if not room.get("climate_control_enabled", True):
            return HVACMode.OFF
        has_heat, has_cool = self._fallback_capabilities()
        climate_mode = room.get("climate_mode", CLIMATE_MODE_AUTO)
        if climate_mode == CLIMATE_MODE_HEAT_ONLY:
            return HVACMode.HEAT if has_heat else HVACMode.OFF
        if climate_mode == CLIMATE_MODE_COOL_ONLY:
            return HVACMode.COOL if has_cool else HVACMode.OFF
        if has_heat and has_cool:
            return HVACMode.HEAT_COOL
        if has_heat:
            return HVACMode.HEAT
        if has_cool:
            return HVACMode.COOL
        return HVACMode.OFF

    def _supports_target_range(self) -> bool:
        """Return True when the room hardware supports dual targets."""
        live = self._get_live_climate()
        if isinstance(live.get("supports_target_range"), bool):
            return live["supports_target_range"]
        has_heat, has_cool = self._fallback_capabilities()
        return has_heat and has_cool

    def _supports_schedule_preset(self) -> bool:
        """Return True when the room has schedules configured."""
        room = self._get_room() or {}
        return bool(room.get("schedules"))

    def _get_live_climate(self) -> dict[str, Any]:
        """Return the coordinator-published climate view for this room."""
        room_data = self._get_live_room()
        climate = room_data.get("climate")
        if isinstance(climate, dict):
            return climate

        mode = room_data.get("mode")
        has_heat, has_cool = self._fallback_capabilities()
        if mode == "heating":
            hvac_action = HVACAction.HEATING
        elif mode == "cooling":
            hvac_action = HVACAction.COOLING
        else:
            hvac_action = HVACAction.IDLE

        fallback_hvac_mode = self._fallback_hvac_mode()
        if fallback_hvac_mode == HVACMode.OFF:
            hvac_action = HVACAction.OFF

        return {
            "hvac_mode": fallback_hvac_mode,
            "hvac_action": hvac_action,
            "supported_hvac_modes": self._fallback_supported_hvac_modes(),
            "supports_target_range": has_heat and has_cool,
            "preset_mode": None,
            "active_source": None,
            "current_temperature": room_data.get("current_temp"),
            "target_temperature": room_data.get("target_temp"),
            "target_temperature_low": room_data.get("heat_target"),
            "target_temperature_high": room_data.get("cool_target"),
        }

    @staticmethod
    def _floatish(value: Any) -> float | None:
        """Return a float when value looks numeric."""
        return float(value) if isinstance(value, (int, float)) else None

    def _custom_override_payload(
        self,
        *,
        temperature: float | None = None,
        low: float | None = None,
        high: float | None = None,
        override_until: float | None = None,
    ) -> dict[str, float | None | str]:
        """Build a custom manual-hold payload for the store."""
        if low is not None or high is not None:
            if low is None or high is None:
                raise ValueError("Both low and high targets are required for a range override")
            if low > high:
                low, high = high, low
            return build_room_override_payload(
                TargetTemps(heat=low, cool=high),
                override_until=override_until,
                override_type=OVERRIDE_CUSTOM,
            )
        if temperature is None:
            raise ValueError("Temperature is required for a single-point override")
        if self._supports_target_range():
            return build_room_override_payload(
                TargetTemps(heat=temperature, cool=temperature),
                override_until=override_until,
                override_type=OVERRIDE_CUSTOM,
            )
        return build_room_override_payload(
            TargetTemps(heat=temperature, cool=temperature),
            override_until=override_until,
            override_type=OVERRIDE_CUSTOM,
            prefer_single_value=True,
        )

    @staticmethod
    def _clear_override_payload() -> dict[str, float | None | str]:
        """Build the payload that returns the room to normal policy control."""
        return clear_room_override_payload()

    def _default_override_until(self) -> float | None:
        """Return the default expiry timestamp for climate-created overrides."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        settings = store.get_settings()
        raw_minutes = settings.get(
            SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES,
            DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
        )
        if not isinstance(raw_minutes, (int, float)):
            minutes = DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES
        else:
            minutes = int(raw_minutes)
        minutes = max(0, min(minutes, MAX_CLIMATE_OVERRIDE_TIMEOUT_MINUTES))
        if minutes == 0:
            return None
        return time.time() + (minutes * 60)


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

    def _set_pending_state(self, **expected: Any) -> None:
        """Store optimistic state shown until the live climate view confirms it."""
        self._pending_state = {"requested_at": time.time(), "expected": expected}
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()

    def _clear_pending_state(self) -> None:
        """Clear optimistic state."""
        self._pending_state = None

    def _pending_value(self, key: str) -> Any:
        """Return a pending field when one exists."""
        if not self._pending_state:
            return None
        return self._pending_state["expected"].get(key)

    def _pending_has(self, key: str) -> bool:
        """Return True when the pending state explicitly contains a field."""
        if not self._pending_state:
            return False
        return key in self._pending_state["expected"]

    def _matches_live_value(self, expected: Any, live: Any) -> bool:
        """Return True when live state matches the expected optimistic value."""
        if isinstance(expected, (int, float)) and isinstance(live, (int, float)):
            return abs(float(expected) - float(live)) <= _FLOAT_TOLERANCE
        return expected == live

    def _pending_matches_live(self) -> bool:
        """Return True when live climate data reflects the pending command."""
        if not self._pending_state:
            return False
        live = self._get_live_climate()
        expected = self._pending_state["expected"]
        if not expected:
            return False
        for key, value in expected.items():
            if not self._matches_live_value(value, live.get(key)):
                return False
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose pending state so dashboards can show a processing indicator."""
        live = self._get_live_climate()
        live_room = self._get_live_room()
        attrs: dict[str, Any] = {}
        active_source = self._pending_value("active_source") if self._pending_has("active_source") else live.get("active_source")
        if active_source is not None:
            attrs["active_source"] = active_source

        if self._pending_has("override_until") or self._pending_has("override_type"):
            override_until = self._pending_value("override_until")
            override_type = self._pending_value("override_type")
        else:
            override_until = live_room.get("override_until")
            override_type = live_room.get("override_type")

        override_active = override_type is not None or override_until is not None
        attrs["override_active"] = override_active
        if override_until is not None:
            attrs["override_until"] = override_until
            attrs["override_remaining_minutes"] = max(0, round((float(override_until) - time.time()) / 60))
        if override_type is not None:
            attrs["override_type"] = override_type
        if self._pending_state:
            attrs["pending_update"] = True
            attrs["pending_since"] = self._pending_state["requested_at"]
        return attrs or None

    @property
    def assumed_state(self) -> bool:
        """Return True while showing optimistic state."""
        return self._pending_state is not None

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state once the live climate view confirms it."""
        if self._pending_state:
            age = time.time() - self._pending_state["requested_at"]
            if self._pending_matches_live() or age > _PENDING_TIMEOUT_S:
                self._clear_pending_state()
        if getattr(self, "hass", None) is not None:
            super()._handle_coordinator_update()

    def _preset_payload(self, preset_mode: str) -> tuple[dict[str, float | None | str], dict[str, Any]]:
        """Build the payload and optimistic state for a preset selection."""
        room = self._get_room() or {}
        supports_range = self._supports_target_range()
        override_until = self._default_override_until()

        if preset_mode == PRESET_SCHEDULE:
            return self._clear_override_payload(), {
                "preset_mode": PRESET_SCHEDULE,
                "active_source": CLIMATE_SOURCE_SCHEDULE,
                "override_until": None,
                "override_type": None,
            }

        if preset_mode == PRESET_OVERRIDE:
            if supports_range:
                low = self.target_temperature_low
                high = self.target_temperature_high
                if low is None or high is None:
                    raise ValueError("Range targets are unavailable for override preset")
                return self._custom_override_payload(low=low, high=high, override_until=override_until), {
                    "active_source": CLIMATE_SOURCE_MANUAL_HOLD,
                    "preset_mode": PRESET_OVERRIDE,
                    "target_temperature_low": low,
                    "target_temperature_high": high,
                    "override_until": override_until,
                    "override_type": OVERRIDE_CUSTOM,
                }
            temperature = self.target_temperature
            return self._custom_override_payload(temperature=temperature, override_until=override_until), {
                "active_source": CLIMATE_SOURCE_MANUAL_HOLD,
                "preset_mode": PRESET_OVERRIDE,
                "target_temperature": temperature,
                "override_until": override_until,
                "override_type": OVERRIDE_CUSTOM,
            }

        if preset_mode == PRESET_COMFORT:
            heat_target = float(room.get("comfort_heat", room.get("comfort_temp", DEFAULT_COMFORT_HEAT)))
            cool_target = float(room.get("comfort_cool", DEFAULT_COMFORT_COOL))
            override_type = OVERRIDE_BOOST
        elif preset_mode == PRESET_ECO:
            heat_target = float(room.get("eco_heat", room.get("eco_temp", DEFAULT_ECO_HEAT)))
            cool_target = float(room.get("eco_cool", DEFAULT_ECO_COOL))
            override_type = OVERRIDE_ECO
        else:
            raise ValueError(f"Unsupported preset mode: {preset_mode}")

        if supports_range:
            return (
                build_room_override_payload(
                    TargetTemps(heat=heat_target, cool=cool_target),
                    override_until=override_until,
                    override_type=override_type,
                ),
                {
                    "active_source": (
                        CLIMATE_SOURCE_COMFORT_HOLD if preset_mode == PRESET_COMFORT else CLIMATE_SOURCE_ECO_HOLD
                    ),
                    "preset_mode": preset_mode,
                    "target_temperature_low": heat_target,
                    "target_temperature_high": cool_target,
                    "override_until": override_until,
                    "override_type": override_type,
                },
            )

        target = cool_target if self._fallback_hvac_mode() == HVACMode.COOL else heat_target
        return (
            build_room_override_payload(
                TargetTemps(heat=target, cool=target),
                override_until=override_until,
                override_type=override_type,
                prefer_single_value=True,
            ),
            {
                "active_source": (
                    CLIMATE_SOURCE_COMFORT_HOLD if preset_mode == PRESET_COMFORT else CLIMATE_SOURCE_ECO_HOLD
                ),
                "preset_mode": preset_mode,
                "target_temperature": target,
                "override_until": override_until,
                "override_type": override_type,
            },
        )

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return supported HVAC modes for this room."""
        pending_mode = self._pending_value("hvac_modes")
        if pending_mode is not None:
            return list(pending_mode)
        live_modes = self._get_live_climate().get("supported_hvac_modes")
        if isinstance(live_modes, list):
            return [HVACMode(mode) for mode in live_modes]
        if isinstance(live_modes, tuple):
            return [HVACMode(mode) for mode in live_modes]
        return self._fallback_supported_hvac_modes()

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported features for this room climate."""
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.PRESET_MODE
        if self._supports_target_range():
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def preset_modes(self) -> list[str]:
        """Return supported preset modes for this room climate."""
        modes = [PRESET_COMFORT, PRESET_ECO, PRESET_OVERRIDE]
        if self._supports_schedule_preset():
            modes.append(PRESET_SCHEDULE)
        return modes

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        pending = self._pending_value("preset_mode")
        if pending is not None:
            return pending
        live = self._get_live_climate().get("preset_mode")
        return str(live) if live is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the configured room HVAC mode."""
        pending = self._pending_value("hvac_mode")
        if pending is not None:
            return HVACMode(pending)
        live = self._get_live_climate().get("hvac_mode")
        if live is not None:
            return HVACMode(live)
        return self._fallback_hvac_mode()

    @property
    def hvac_action(self) -> HVACAction:
        """Return the room's current live HVAC action."""
        live = self._get_live_climate().get("hvac_action")
        if live is not None:
            return HVACAction(live)
        return HVACAction.OFF if self.hvac_mode == HVACMode.OFF else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Return the room's current temperature from coordinator data."""
        live = self._floatish(self._get_live_climate().get("current_temperature"))
        if live is not None:
            return live
        return self._floatish(self._get_live_room().get("current_temp"))

    @property
    def target_temperature(self) -> float:
        """Return the room's effective single-point target."""
        pending = self._floatish(self._pending_value("target_temperature"))
        if pending is not None:
            return pending
        live = self._floatish(self._get_live_climate().get("target_temperature"))
        if live is not None:
            return live
        room = self._get_room() or {}
        override_heat = self._floatish(room.get("override_heat"))
        if override_heat is not None:
            return override_heat
        override_cool = self._floatish(room.get("override_cool"))
        if override_cool is not None:
            return override_cool
        if self.hvac_mode == HVACMode.COOL:
            cool = self._floatish(room.get("comfort_cool"))
            if cool is not None:
                return cool
        comfort = self._floatish(room.get("comfort_heat", room.get("comfort_temp")))
        return comfort if comfort is not None else DEFAULT_COMFORT_TEMP

    @property
    def target_temperature_low(self) -> float | None:
        """Return the effective heating target for heat/cool rooms."""
        if not self._supports_target_range():
            return None
        pending = self._floatish(self._pending_value("target_temperature_low"))
        if pending is not None:
            return pending
        live = self._floatish(self._get_live_climate().get("target_temperature_low"))
        if live is not None:
            return live
        room = self._get_room() or {}
        return self._floatish(room.get("comfort_heat", room.get("comfort_temp"))) or DEFAULT_COMFORT_HEAT

    @property
    def target_temperature_high(self) -> float | None:
        """Return the effective cooling target for heat/cool rooms."""
        if not self._supports_target_range():
            return None
        pending = self._floatish(self._pending_value("target_temperature_high"))
        if pending is not None:
            return pending
        live = self._floatish(self._get_live_climate().get("target_temperature_high"))
        if live is not None:
            return live
        room = self._get_room() or {}
        return self._floatish(room.get("comfort_cool")) or DEFAULT_COMFORT_COOL

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Create or update a manual hold at the requested target."""
        store = self.coordinator.hass.data[DOMAIN]["store"]

        target_temp_low = kwargs.get("target_temp_low")
        target_temp_high = kwargs.get("target_temp_high")
        if target_temp_low is not None or target_temp_high is not None:
            low = float(target_temp_low) if target_temp_low is not None else self.target_temperature_low
            high = float(target_temp_high) if target_temp_high is not None else self.target_temperature_high
            if low is None or high is None:
                return
            override_until = self._default_override_until()
            _LOGGER.debug(
                "Area '%s': %s received range override request via climate entity "
                "(target_temp_low=%s, target_temp_high=%s, resolved_low=%s, resolved_high=%s, "
                "hvac_mode=%s, supports_range=%s, override_until=%s)",
                self._area_id,
                self.__class__.__name__,
                target_temp_low,
                target_temp_high,
                low,
                high,
                self.hvac_mode,
                self._supports_target_range(),
                override_until,
            )
            await store.async_update_room(
                self._area_id,
                self._custom_override_payload(low=low, high=high, override_until=override_until),
            )
            self._set_pending_state(
                active_source=CLIMATE_SOURCE_MANUAL_HOLD,
                preset_mode=PRESET_OVERRIDE,
                target_temperature_low=low,
                target_temperature_high=high,
                override_until=override_until,
                override_type=OVERRIDE_CUSTOM,
            )
            await self.coordinator.async_request_refresh()
            return

        temperature = kwargs.get("temperature")
        if temperature is None:
            return
        value = float(temperature)
        override_until = self._default_override_until()
        if self._supports_target_range():
            _LOGGER.info(
                "Area '%s': %s received single-temperature override request on a dual-target room "
                "(temperature=%s, hvac_mode=%s, override_until=%s). "
                "RoomMind will apply this to both heat and cool override targets; "
                "use target_temp_low/target_temp_high to change only one side.",
                self._area_id,
                self.__class__.__name__,
                value,
                self.hvac_mode,
                override_until,
            )
        else:
            _LOGGER.debug(
                "Area '%s': %s received single-temperature override request "
                "(temperature=%s, hvac_mode=%s, override_until=%s)",
                self._area_id,
                self.__class__.__name__,
                value,
                self.hvac_mode,
                override_until,
            )
        await store.async_update_room(
            self._area_id,
            self._custom_override_payload(temperature=value, override_until=override_until),
        )
        pending: dict[str, Any] = {
            "active_source": CLIMATE_SOURCE_MANUAL_HOLD,
            "preset_mode": PRESET_OVERRIDE,
            "target_temperature": value,
            "override_until": override_until,
            "override_type": OVERRIDE_CUSTOM,
        }
        if self._supports_target_range():
            pending["target_temperature_low"] = value
            pending["target_temperature_high"] = value
        self._set_pending_state(**pending)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the active climate preset mode."""
        if preset_mode not in self.preset_modes:
            raise ValueError(f"Unsupported preset mode: {preset_mode}")
        store = self.coordinator.hass.data[DOMAIN]["store"]
        payload, pending = self._preset_payload(preset_mode)
        await store.async_update_room(self._area_id, payload)
        self._set_pending_state(**pending)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Update the room's selected HVAC mode."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        if hvac_mode == HVACMode.OFF:
            payload = {"climate_control_enabled": False}
        elif hvac_mode == HVACMode.HEAT:
            payload = {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_HEAT_ONLY}
        elif hvac_mode == HVACMode.COOL:
            payload = {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_COOL_ONLY}
        elif hvac_mode == HVACMode.HEAT_COOL:
            payload = {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_AUTO}
        else:
            raise ValueError(f"Unsupported hvac mode: {hvac_mode}")
        await store.async_update_room(self._area_id, payload)
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


class RoomMindOverrideClimate(RoomMindRoomClimate):
    """Legacy override climate backed by the room climate source of truth."""

    _attr_icon = "mdi:thermometer-alert"

    def __init__(self, coordinator: RoomMindCoordinator, area_id: str) -> None:
        super().__init__(coordinator, area_id)
        self._attr_unique_id = f"{DOMAIN}_{area_id}_override"
        self._attr_name = f"{get_area_name(coordinator.hass, area_id)} Override"
        self._attr_translation_key = None
        self.entity_id = f"climate.{DOMAIN}_{area_id}_override"

    @staticmethod
    def _is_hold_source(active_source: str | None) -> bool:
        """Return True when the climate source represents a user hold."""
        return active_source in {
            CLIMATE_SOURCE_COMFORT_HOLD,
            CLIMATE_SOURCE_ECO_HOLD,
            CLIMATE_SOURCE_MANUAL_HOLD,
        }

    def _is_override_active(self) -> bool:
        """Return True when a RoomMind hold is active for the room."""
        pending_source = self._pending_value("active_source")
        if self._is_hold_source(pending_source):
            return True
        pending_hvac = self._pending_value("hvac_mode")
        if pending_hvac == HVACMode.AUTO:
            return True
        if pending_hvac == HVACMode.OFF:
            return False

        room_override_live = build_override_live(self._get_room() or {})
        if room_override_live.get("override_active"):
            return True
        return self._is_hold_source(self._get_live_climate().get("active_source"))

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Expose the legacy AUTO/OFF interface."""
        return [HVACMode.OFF, HVACMode.AUTO]

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Expose the legacy override climate feature set."""
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._supports_target_range():
            features |= ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        else:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def hvac_mode(self) -> HVACMode:
        """Return AUTO when a RoomMind hold is active, OFF otherwise."""
        pending = self._pending_value("hvac_mode")
        if pending is not None:
            return HVACMode(pending)
        return HVACMode.AUTO if self._is_override_active() else HVACMode.OFF

    @property
    def target_temperature(self) -> float:
        """Return the active override target for backward compatibility."""
        pending = self._floatish(self._pending_value("target_temperature"))
        if pending is not None:
            return pending

        room_override_live = build_override_live(self._get_room() or {})
        if self._is_override_active():
            live_target = self._floatish(self._get_live_climate().get("target_temperature"))
            if live_target is not None:
                return live_target
            override_heat = self._floatish(room_override_live.get("override_heat"))
            if override_heat is not None:
                return override_heat
            override_cool = self._floatish(room_override_live.get("override_cool"))
            if override_cool is not None:
                return override_cool

        return DEFAULT_COMFORT_TEMP

    @property
    def target_temperature_low(self) -> float | None:
        """Return the active override heating target for dual-target rooms."""
        if not self._supports_target_range():
            return None

        pending = self._floatish(self._pending_value("target_temperature_low"))
        if pending is not None:
            return pending

        room_override_live = build_override_live(self._get_room() or {})
        override_heat = self._floatish(room_override_live.get("override_heat"))
        if override_heat is not None:
            return override_heat

        if self._is_override_active():
            live_low = self._floatish(self._get_live_climate().get("target_temperature_low"))
            if live_low is not None:
                return live_low

        return RoomMindRoomClimate.target_temperature_low.fget(self)

    @property
    def target_temperature_high(self) -> float | None:
        """Return the active override cooling target for dual-target rooms."""
        if not self._supports_target_range():
            return None

        pending = self._floatish(self._pending_value("target_temperature_high"))
        if pending is not None:
            return pending

        room_override_live = build_override_live(self._get_room() or {})
        override_cool = self._floatish(room_override_live.get("override_cool"))
        if override_cool is not None:
            return override_cool

        if self._is_override_active():
            live_high = self._floatish(self._get_live_climate().get("target_temperature_high"))
            if live_high is not None:
                return live_high

        return RoomMindRoomClimate.target_temperature_high.fget(self)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Create a legacy-style indefinite manual override."""
        target_temp_low = kwargs.get("target_temp_low")
        target_temp_high = kwargs.get("target_temp_high")
        if target_temp_low is not None or target_temp_high is not None:
            low = float(target_temp_low) if target_temp_low is not None else self.target_temperature_low
            high = float(target_temp_high) if target_temp_high is not None else self.target_temperature_high
            if low is None or high is None:
                return

            store = self.coordinator.hass.data[DOMAIN]["store"]
            _LOGGER.debug(
                "Area '%s': %s received range override request via legacy climate entity "
                "(target_temp_low=%s, target_temp_high=%s, resolved_low=%s, resolved_high=%s, "
                "override_until=None)",
                self._area_id,
                self.__class__.__name__,
                target_temp_low,
                target_temp_high,
                low,
                high,
            )
            await store.async_update_room(
                self._area_id,
                self._custom_override_payload(low=low, high=high, override_until=None),
            )
            self._set_pending_state(
                active_source=CLIMATE_SOURCE_MANUAL_HOLD,
                preset_mode=PRESET_OVERRIDE,
                hvac_mode=HVACMode.AUTO,
                target_temperature_low=low,
                target_temperature_high=high,
                override_until=None,
                override_type=OVERRIDE_CUSTOM,
            )
            await self.coordinator.async_request_refresh()
            return

        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        value = float(temperature)
        store = self.coordinator.hass.data[DOMAIN]["store"]
        if self._supports_target_range():
            _LOGGER.info(
                "Area '%s': %s received single-temperature override request on a dual-target room "
                "(temperature=%s, override_until=None). "
                "RoomMind will apply this to both heat and cool override targets; "
                "use target_temp_low/target_temp_high to change only one side.",
                self._area_id,
                self.__class__.__name__,
                value,
            )
        else:
            _LOGGER.debug(
                "Area '%s': %s received single-temperature override request "
                "(temperature=%s, override_until=None)",
                self._area_id,
                self.__class__.__name__,
                value,
            )
        await store.async_update_room(
            self._area_id,
            self._custom_override_payload(temperature=value, override_until=None),
        )
        pending: dict[str, Any] = {
            "active_source": CLIMATE_SOURCE_MANUAL_HOLD,
            "preset_mode": PRESET_OVERRIDE,
            "hvac_mode": HVACMode.AUTO,
            "target_temperature": value,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        }
        if self._supports_target_range():
            pending["target_temperature_low"] = value
            pending["target_temperature_high"] = value
        self._set_pending_state(**pending)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Bridge legacy AUTO/OFF calls onto the new room-climate model."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        if hvac_mode == HVACMode.OFF:
            await store.async_update_room(self._area_id, self._clear_override_payload())
            self._set_pending_state(
                hvac_mode=HVACMode.OFF,
                active_source=None,
                preset_mode=None,
                override_until=None,
                override_type=None,
            )
            await self.coordinator.async_request_refresh()
            return

        if hvac_mode != HVACMode.AUTO:
            raise ValueError(f"Unsupported hvac mode: {hvac_mode}")

        if self._supports_target_range():
            low = RoomMindRoomClimate.target_temperature_low.fget(self)
            high = RoomMindRoomClimate.target_temperature_high.fget(self)
            if low is None or high is None:
                raise ValueError("Range targets are unavailable for override activation")
            payload = self._custom_override_payload(low=low, high=high, override_until=None)
            pending: dict[str, Any] = {
                "active_source": CLIMATE_SOURCE_MANUAL_HOLD,
                "preset_mode": PRESET_OVERRIDE,
                "hvac_mode": HVACMode.AUTO,
                "target_temperature_low": low,
                "target_temperature_high": high,
                "override_until": None,
                "override_type": OVERRIDE_CUSTOM,
            }
        else:
            temperature = RoomMindRoomClimate.target_temperature.fget(self)
            payload = self._custom_override_payload(temperature=temperature, override_until=None)
            pending = {
                "active_source": CLIMATE_SOURCE_MANUAL_HOLD,
                "preset_mode": PRESET_OVERRIDE,
                "hvac_mode": HVACMode.AUTO,
                "target_temperature": temperature,
                "override_until": None,
                "override_type": OVERRIDE_CUSTOM,
            }

        await store.async_update_room(self._area_id, payload)
        self._set_pending_state(**pending)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the legacy override climate on."""
        del kwargs
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the legacy override climate off."""
        del kwargs
        await self.async_set_hvac_mode(HVACMode.OFF)
