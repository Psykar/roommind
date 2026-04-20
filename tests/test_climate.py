"""Tests for RoomMind climate platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode

from custom_components.roommind import climate as climate_module
from custom_components.roommind.climate import (
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_OVERRIDE,
    PRESET_SCHEDULE,
    RoomMindOverrideClimate,
    RoomMindRoomClimate,
    _create_room_climates,
    async_setup_entry,
)
from custom_components.roommind.const import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL_ONLY,
    CLIMATE_MODE_HEAT_ONLY,
    DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
    DOMAIN,
    OVERRIDE_BOOST,
    OVERRIDE_CUSTOM,
    OVERRIDE_ECO,
    PRESET_AWAY,
    PRESET_VACATION,
    SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.hass.states.get = MagicMock(return_value=None)
    store = MagicMock()
    store.async_update_room = AsyncMock()
    store.async_save_room = AsyncMock()
    store.get_settings.return_value = {}
    coordinator.hass.data = {DOMAIN: {"store": store}}
    coordinator.data = {}
    return coordinator, store


def _mixed_room(**overrides):
    room = {
        "devices": [
            {"entity_id": "climate.trv", "type": "trv"},
            {"entity_id": "climate.ac", "type": "ac"},
        ],
        "climate_mode": CLIMATE_MODE_AUTO,
        "climate_control_enabled": True,
        "comfort_heat": 21.0,
        "comfort_cool": 24.0,
        "eco_heat": 17.0,
        "eco_cool": 27.0,
        "schedules": [{"entity_id": "schedule.living_room"}],
    }
    room.update(overrides)
    return room


def _climate_view(**overrides):
    climate = {
        "hvac_mode": HVACMode.HEAT_COOL,
        "hvac_action": HVACAction.IDLE,
        "supported_hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL],
        "supports_target_range": True,
        "preset_mode": None,
        "active_source": "comfort",
        "current_temperature": 20.5,
        "target_temperature": 21.0,
        "target_temperature_low": 21.0,
        "target_temperature_high": 24.0,
    }
    climate.update(overrides)
    return climate


def _override_until(now: float, minutes: int = 120) -> float:
    return now + (minutes * 60)


def test_create_room_climates(mock_coordinator):
    """Factory creates the primary and compatibility climate entities."""
    coordinator, _ = mock_coordinator
    climates = _create_room_climates(coordinator, "living_room")
    assert len(climates) == 2
    assert isinstance(climates[0], RoomMindRoomClimate)
    assert isinstance(climates[1], RoomMindOverrideClimate)


def test_room_climate_unique_id_and_entity_id(mock_coordinator):
    """Room climate entity has stable ids."""
    coordinator, _ = mock_coordinator
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.unique_id == "roommind_living_room_climate"
    assert entity.entity_id == "climate.roommind_living_room"
    assert entity.translation_key == "room_climate"


def test_override_climate_unique_id_and_entity_id(mock_coordinator):
    """Legacy override climate keeps its original ids."""
    coordinator, _ = mock_coordinator
    entity = RoomMindOverrideClimate(coordinator, "living_room")
    assert entity.unique_id == "roommind_living_room_override"
    assert entity.entity_id == "climate.roommind_living_room_override"


def test_room_climate_name_uses_area_registry_name(mock_coordinator, monkeypatch):
    """Room climate entity name prefers the Home Assistant area name."""
    coordinator, _ = mock_coordinator
    monkeypatch.setattr(climate_module, "get_area_name", lambda hass, area_id: "Living Room")

    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.name == "Living Room Climate"


def test_override_climate_name_uses_area_registry_name(mock_coordinator, monkeypatch):
    """Legacy override entity still uses the area registry name."""
    coordinator, _ = mock_coordinator
    monkeypatch.setattr(climate_module, "get_area_name", lambda hass, area_id: "Living Room")

    entity = RoomMindOverrideClimate(coordinator, "living_room")

    assert entity.name == "Living Room Override"


def test_room_climate_supported_modes_are_hardware_driven(mock_coordinator):
    """Supported modes stay tied to hardware even when heat-only is selected."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(climate_mode=CLIMATE_MODE_HEAT_ONLY)
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(hvac_mode=HVACMode.HEAT),
            }
        }
    }

    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    assert entity.hvac_mode == HVACMode.HEAT


def test_room_climate_hvac_mode_cool_only(mock_coordinator):
    """Cool-only rooms surface the selected cooling mode and action."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.ac", "type": "ac"}],
        "climate_mode": CLIMATE_MODE_COOL_ONLY,
        "climate_control_enabled": True,
    }
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(
                    hvac_mode=HVACMode.COOL,
                    hvac_action=HVACAction.COOLING,
                    supported_hvac_modes=[HVACMode.OFF, HVACMode.COOL],
                    supports_target_range=False,
                    target_temperature=25.0,
                    target_temperature_low=None,
                    target_temperature_high=None,
                )
            }
        }
    }

    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.hvac_mode == HVACMode.COOL
    assert entity.target_temperature == 25.0
    assert entity.hvac_action == HVACAction.COOLING


def test_room_climate_hvac_mode_off_when_disabled(mock_coordinator):
    """Disabled room climate control surfaces as OFF."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": False,
    }
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(
                    hvac_mode=HVACMode.OFF,
                    hvac_action=HVACAction.OFF,
                    supported_hvac_modes=[HVACMode.OFF, HVACMode.HEAT],
                    supports_target_range=False,
                )
            }
        }
    }

    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.hvac_mode == HVACMode.OFF
    assert entity.hvac_action == HVACAction.OFF


def test_room_climate_pending_state_marks_entity_assumed(mock_coordinator):
    """Pending optimistic state is surfaced via assumed_state and extra attrs."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    entity = RoomMindRoomClimate(coordinator, "living_room")

    entity._set_pending_state(hvac_mode=HVACMode.OFF)

    assert entity.assumed_state is True
    assert entity.extra_state_attributes is not None
    assert entity.extra_state_attributes["pending_update"] is True
    assert entity.hvac_mode == HVACMode.OFF


def test_room_climate_pending_indefinite_override_marks_override_active(mock_coordinator):
    """Pending indefinite holds still surface as active in climate attributes."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    entity = RoomMindRoomClimate(coordinator, "living_room")

    entity._set_pending_state(
        active_source="manual_hold",
        preset_mode=PRESET_OVERRIDE,
        target_temperature=22.0,
        override_until=None,
        override_type=OVERRIDE_CUSTOM,
    )

    assert entity.extra_state_attributes == {
        "active_source": "manual_hold",
        "override_active": True,
        "override_type": OVERRIDE_CUSTOM,
        "pending_update": True,
        "pending_since": entity._pending_state["requested_at"],
    }


def test_room_climate_uses_live_climate_view(mock_coordinator):
    """Room climate reads its state from the coordinator's climate view."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(
                    hvac_action=HVACAction.HEATING,
                    target_temperature=21.5,
                    target_temperature_low=20.0,
                    target_temperature_high=24.5,
                )
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.target_temperature == 21.5
    assert entity.target_temperature_low == 20.0
    assert entity.target_temperature_high == 24.5
    assert entity.current_temperature == 20.5
    assert entity.hvac_action == HVACAction.HEATING


def test_room_climate_coordinator_update_keeps_pending_until_match(mock_coordinator):
    """Optimistic state survives unrelated coordinator updates."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(preset_mode=PRESET_COMFORT, target_temperature_low=21.0),
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    entity._set_pending_state(preset_mode=PRESET_OVERRIDE, target_temperature_low=19.0)
    entity._handle_coordinator_update()

    assert entity.assumed_state is True
    assert entity.preset_mode == PRESET_OVERRIDE


def test_room_climate_coordinator_update_clears_pending_state_on_match(mock_coordinator):
    """Coordinator refresh confirmation clears the optimistic pending state."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(preset_mode=PRESET_OVERRIDE, target_temperature_low=19.0),
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    entity._set_pending_state(preset_mode=PRESET_OVERRIDE, target_temperature_low=19.0)
    entity._handle_coordinator_update()

    assert entity.assumed_state is False
    assert entity.extra_state_attributes == {"active_source": "comfort", "override_active": False}


def test_room_climate_preset_modes_include_schedule_only_when_scheduled(mock_coordinator):
    """Preset list exposes schedule only for rooms with schedules."""
    coordinator, store = mock_coordinator
    entity = RoomMindRoomClimate(coordinator, "living_room")

    store.get_room.return_value = _mixed_room()
    assert entity.preset_modes == [PRESET_COMFORT, PRESET_ECO, PRESET_OVERRIDE, PRESET_SCHEDULE]

    store.get_room.return_value = _mixed_room(schedules=[])
    assert entity.preset_modes == [PRESET_COMFORT, PRESET_ECO, PRESET_OVERRIDE]


@pytest.mark.parametrize(
    ("live_climate", "expected"),
    [
        (_climate_view(preset_mode=PRESET_COMFORT, active_source="comfort_hold"), PRESET_COMFORT),
        (_climate_view(preset_mode=PRESET_ECO, active_source="eco_hold"), PRESET_ECO),
        (_climate_view(preset_mode=PRESET_OVERRIDE, active_source="manual_hold"), PRESET_OVERRIDE),
        (_climate_view(preset_mode=PRESET_SCHEDULE, active_source="schedule"), PRESET_SCHEDULE),
        (_climate_view(preset_mode=PRESET_VACATION, active_source="vacation"), PRESET_VACATION),
        (_climate_view(preset_mode=PRESET_AWAY, active_source="presence_away"), PRESET_AWAY),
        (_climate_view(preset_mode=None, active_source="comfort"), None),
    ],
)
def test_room_climate_preset_mode_comes_from_live_climate(mock_coordinator, live_climate, expected):
    """Preset mode reflects the coordinator's resolved climate source."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {"rooms": {"living_room": {"climate": live_climate}}}
    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.preset_mode == expected


def test_override_climate_hvac_mode_tracks_hold_sources(mock_coordinator):
    """Legacy override climate reports AUTO while a hold is active."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(override_heat_temp=21.0, override_cool_temp=24.0, override_type=OVERRIDE_BOOST)
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(preset_mode=PRESET_COMFORT, active_source="comfort_hold"),
            }
        }
    }

    entity = RoomMindOverrideClimate(coordinator, "living_room")

    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.AUTO]
    assert entity.hvac_mode == HVACMode.AUTO


def test_override_climate_hvac_mode_off_when_no_hold(mock_coordinator):
    """Legacy override climate reports OFF when RoomMind is following policy."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(preset_mode=PRESET_SCHEDULE, active_source="schedule"),
            }
        }
    }

    entity = RoomMindOverrideClimate(coordinator, "living_room")

    assert entity.hvac_mode == HVACMode.OFF


def test_override_climate_supported_features_use_range_for_dual_target_rooms(mock_coordinator):
    """Legacy override climate exposes range targets when the room supports them."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()

    entity = RoomMindOverrideClimate(coordinator, "living_room")

    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    assert not (entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)


def test_override_climate_target_temperature_uses_live_effective_target(mock_coordinator):
    """Legacy override climate falls back to the live target when split overrides are active."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(
        override_heat_temp=19.5,
        override_cool_temp=25.5,
        override_type=OVERRIDE_CUSTOM,
    )
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(
                    preset_mode=PRESET_OVERRIDE,
                    active_source="manual_hold",
                    target_temperature=19.5,
                    target_temperature_low=19.5,
                    target_temperature_high=25.5,
                )
            }
        }
    }

    entity = RoomMindOverrideClimate(coordinator, "living_room")

    assert entity.target_temperature == 19.5


def test_override_climate_target_range_uses_active_split_override(mock_coordinator):
    """Legacy override climate surfaces active split override targets."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(
        override_heat_temp=19.5,
        override_cool_temp=25.5,
        override_type=OVERRIDE_CUSTOM,
    )

    entity = RoomMindOverrideClimate(coordinator, "living_room")

    assert entity.target_temperature_low == 19.5
    assert entity.target_temperature_high == 25.5


@pytest.mark.asyncio
async def test_room_climate_set_temperature_range_stores_split_override(mock_coordinator, monkeypatch):
    """Range updates create a custom split manual hold."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(schedules=[])
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(target_temp_low=20.0, target_temp_high=24.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 20.0,
            "override_cool_temp": 24.0,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_room_climate_set_temperature_single_point(mock_coordinator, monkeypatch):
    """Single target updates create a custom single-point manual hold."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(temperature=22.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": 22.0,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    store.async_save_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_room_climate_set_temperature_single_point_range_room_stores_split_override(
    mock_coordinator, monkeypatch
):
    """Single-point updates on dual-target rooms store equal heat/cool overrides."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(schedules=[])
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(temperature=22.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 22.0,
            "override_cool_temp": 22.0,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_CUSTOM,
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_temperature_uses_indefinite_override_when_default_is_zero(
    mock_coordinator, monkeypatch
):
    """A zero default timeout keeps climate-created overrides indefinite."""
    coordinator, store = mock_coordinator
    store.get_settings.return_value = {SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES: 0}
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
    monkeypatch.setattr(climate_module.time, "time", lambda: 1_700_000_000.0)
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(temperature=22.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": 22.0,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_temperature_does_not_mutate_comfort_defaults(mock_coordinator, monkeypatch):
    """Direct thermostat changes stay manual even during a comfort hold."""
    coordinator, store = mock_coordinator
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    store.get_room.return_value = _mixed_room(override_heat_temp=21.0, override_cool_temp=24.0, override_type=OVERRIDE_BOOST)
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(preset_mode=PRESET_COMFORT, active_source="comfort_hold")
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(target_temp_low=19.0, target_temp_high=25.0)

    store.async_save_room.assert_not_awaited()
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 19.0,
            "override_cool_temp": 25.0,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_CUSTOM,
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_comfort(mock_coordinator, monkeypatch):
    """Comfort preset writes the stored comfort targets as a boost hold."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_COMFORT)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 21.0,
            "override_cool_temp": 24.0,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_BOOST,
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()
    assert entity.preset_mode == PRESET_COMFORT
    assert entity.target_temperature_low == 21.0
    assert entity.target_temperature_high == 24.0
    assert entity.assumed_state is True


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_eco(mock_coordinator, monkeypatch):
    """Eco preset writes the stored eco targets as an eco hold."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_ECO)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 17.0,
            "override_cool_temp": 27.0,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_ECO,
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_override_freezes_current_targets(mock_coordinator, monkeypatch):
    """Override preset freezes the current effective targets into a custom override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(schedules=[])
    now = 1_700_000_000.0
    monkeypatch.setattr(climate_module.time, "time", lambda: now)
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(
                    preset_mode=None,
                    target_temperature=19.5,
                    target_temperature_low=19.5,
                    target_temperature_high=25.5,
                )
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_OVERRIDE)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 19.5,
            "override_cool_temp": 25.5,
            "override_until": _override_until(now),
            "override_type": OVERRIDE_CUSTOM,
        },
    )


def test_room_climate_extra_attributes_include_override_timeout(mock_coordinator):
    """Climate attrs expose override timeout details for dashboards."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {
        "rooms": {
            "living_room": {
                "override_until": 1_700_007_200.0,
                "override_type": OVERRIDE_CUSTOM,
                "climate": _climate_view(active_source="manual_hold"),
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    attrs = entity.extra_state_attributes

    assert attrs is not None
    assert attrs["active_source"] == "manual_hold"
    assert attrs["override_active"] is True
    assert attrs["override_until"] == 1_700_007_200.0
    assert attrs["override_type"] == OVERRIDE_CUSTOM


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_schedule_clears_override(mock_coordinator):
    """Schedule preset clears any active RoomMind override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(
        override_heat_temp=21.0,
        override_cool_temp=24.0,
        override_type=OVERRIDE_BOOST,
    )
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_SCHEDULE)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": None,
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_hvac_mode_updates_room_mode(mock_coordinator):
    """Room climate HVAC writes update climate_control_enabled and climate_mode."""
    coordinator, store = mock_coordinator
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {"climate_control_enabled": True, "climate_mode": CLIMATE_MODE_AUTO},
    )
    assert entity.hvac_mode == HVACMode.HEAT_COOL
    assert entity.assumed_state is True


@pytest.mark.asyncio
async def test_override_climate_set_temperature_creates_indefinite_manual_hold(mock_coordinator):
    """Legacy override climate keeps its write path indefinite for compatibility."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
    entity = RoomMindOverrideClimate(coordinator, "living_room")

    await entity.async_set_temperature(temperature=22.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": 22.0,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    assert entity.hvac_mode == HVACMode.AUTO


@pytest.mark.asyncio
async def test_override_climate_set_temperature_range_creates_indefinite_manual_hold(mock_coordinator):
    """Legacy override climate stores split targets for dual-target rooms."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    entity = RoomMindOverrideClimate(coordinator, "living_room")

    await entity.async_set_temperature(target_temp_low=19.0, target_temp_high=25.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 19.0,
            "override_cool_temp": 25.0,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    assert entity.hvac_mode == HVACMode.AUTO


@pytest.mark.asyncio
async def test_override_climate_set_hvac_mode_auto_uses_effective_targets(mock_coordinator):
    """Turning the legacy override climate on creates a manual hold from the current room target."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    coordinator.data = {
        "rooms": {
            "living_room": {
                "climate": _climate_view(
                    preset_mode=PRESET_SCHEDULE,
                    active_source="schedule",
                    target_temperature=19.5,
                    target_temperature_low=19.5,
                    target_temperature_high=25.5,
                )
            }
        }
    }
    entity = RoomMindOverrideClimate(coordinator, "living_room")

    await entity.async_set_hvac_mode(HVACMode.AUTO)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 19.5,
            "override_cool_temp": 25.5,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    assert entity.hvac_mode == HVACMode.AUTO


@pytest.mark.asyncio
async def test_override_climate_set_hvac_mode_off_clears_override(mock_coordinator):
    """Turning the legacy override climate off clears the shared override state."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(override_heat_temp=21.0, override_cool_temp=24.0, override_type=OVERRIDE_BOOST)
    entity = RoomMindOverrideClimate(coordinator, "living_room")

    await entity.async_set_hvac_mode(HVACMode.OFF)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": None,
        },
    )
    assert entity.hvac_mode == HVACMode.OFF


@pytest.mark.asyncio
async def test_async_setup_entry_creates_entities_for_all_rooms():
    """async_setup_entry creates the primary and legacy climate entities per room."""
    coordinator = MagicMock()
    coordinator._climate_entity_areas = set()

    store = MagicMock()
    store.get_rooms.return_value = {
        "living_room": {"thermostats": ["climate.living"]},
        "bedroom": {},
    }

    entry = MagicMock()
    entry.entry_id = "test_entry"

    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: coordinator, "store": store}}

    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert coordinator.async_add_climate_entities is async_add_entities
    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 4
    assert sum(isinstance(e, RoomMindRoomClimate) for e in entities) == 4
    assert sum(isinstance(e, RoomMindOverrideClimate) for e in entities) == 2
    assert "living_room" in coordinator._climate_entity_areas
    assert "bedroom" in coordinator._climate_entity_areas


@pytest.mark.asyncio
async def test_async_setup_entry_no_rooms():
    """async_setup_entry does not call async_add_entities when no rooms exist."""
    coordinator = MagicMock()
    coordinator._climate_entity_areas = set()

    store = MagicMock()
    store.get_rooms.return_value = {}

    entry = MagicMock()
    entry.entry_id = "test_entry"

    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: coordinator, "store": store}}

    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_not_called()
