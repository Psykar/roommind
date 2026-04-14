"""Tests for RoomMind climate platform."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode

from custom_components.roommind import climate as climate_module
from custom_components.roommind.climate import (
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_OVERRIDE,
    PRESET_SCHEDULE,
    RoomMindRoomClimate,
    _create_room_climates,
    async_setup_entry,
)
from custom_components.roommind.const import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL_ONLY,
    CLIMATE_MODE_HEAT_ONLY,
    DEFAULT_COMFORT_TEMP,
    DOMAIN,
    OVERRIDE_CUSTOM,
    TargetTemps,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.hass.states.get = MagicMock(return_value=None)
    coordinator._resolve_target_temps = MagicMock(return_value=TargetTemps())
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


def test_create_room_climates(mock_coordinator):
    """Factory creates the room climate entity."""
    coordinator, _ = mock_coordinator
    climates = _create_room_climates(coordinator, "living_room")
    assert len(climates) == 1
    assert isinstance(climates[0], RoomMindRoomClimate)


def test_room_climate_unique_id_and_entity_id(mock_coordinator):
    """Room climate entity has stable ids."""
    coordinator, _ = mock_coordinator
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.unique_id == "roommind_living_room_climate"
    assert entity.entity_id == "climate.roommind_living_room"
    assert entity.translation_key == "room_climate"


def test_room_climate_name_uses_area_registry_name(mock_coordinator, monkeypatch):
    """Room climate entity name prefers the Home Assistant area name."""
    coordinator, _ = mock_coordinator
    monkeypatch.setattr(climate_module, "get_area_name", lambda hass, area_id: "Living Room")

    entity = RoomMindRoomClimate(coordinator, "living_room")

    assert entity.name == "Living Room Climate"


def test_room_climate_hvac_modes_include_heat_cool_for_mixed_room(mock_coordinator):
    """Mixed TRV + AC rooms expose heat, cool, and heat_cool."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [
            {"entity_id": "climate.trv", "type": "trv"},
            {"entity_id": "climate.ac", "type": "ac"},
        ],
        "climate_mode": CLIMATE_MODE_AUTO,
        "climate_control_enabled": True,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL]
    assert entity.hvac_mode == HVACMode.HEAT_COOL
    assert entity.target_temperature_low == DEFAULT_COMFORT_TEMP
    assert entity.target_temperature_high == 24.0


def test_room_climate_hvac_mode_heat_only(mock_coordinator):
    """Heat-only rooms map to HEAT and expose a single target."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.target_temperature_low is None
    assert entity.target_temperature_high is None


def test_room_climate_hvac_mode_cool_only(mock_coordinator):
    """Cool-only rooms map to COOL."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.ac", "type": "ac"}],
        "climate_mode": CLIMATE_MODE_COOL_ONLY,
        "climate_control_enabled": True,
    }
    coordinator.data = {"rooms": {"living_room": {"target_temp": 25.0, "mode": "cooling"}}}
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
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_mode == HVACMode.OFF
    assert entity.hvac_action == HVACAction.OFF


def test_room_climate_uses_live_targets(mock_coordinator):
    """Room climate prefers live targets from coordinator data."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [
            {"entity_id": "climate.trv", "type": "trv"},
            {"entity_id": "climate.ac", "type": "ac"},
        ],
        "climate_mode": CLIMATE_MODE_AUTO,
        "climate_control_enabled": True,
    }
    coordinator.data = {
        "rooms": {
            "living_room": {
                "target_temp": 21.5,
                "heat_target": 20.0,
                "cool_target": 24.5,
                "current_temp": 20.5,
                "mode": "heating",
            }
        }
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.target_temperature == 21.5
    assert entity.target_temperature_low == 20.0
    assert entity.target_temperature_high == 24.5
    assert entity.current_temperature == 20.5
    assert entity.hvac_action == HVACAction.HEATING


def test_room_climate_preset_modes_include_schedule_only_when_scheduled(mock_coordinator):
    """Preset list exposes schedule only for rooms with schedules."""
    coordinator, store = mock_coordinator
    entity = RoomMindRoomClimate(coordinator, "living_room")

    store.get_room.return_value = _mixed_room()
    assert entity.preset_modes == [PRESET_COMFORT, PRESET_ECO, PRESET_OVERRIDE, PRESET_SCHEDULE]

    store.get_room.return_value = _mixed_room(schedules=[])
    assert entity.preset_modes == [PRESET_COMFORT, PRESET_ECO, PRESET_OVERRIDE]


@pytest.mark.parametrize(
    ("room", "expected"),
    [
        (_mixed_room(override_heat_temp=21.0, override_cool_temp=24.0, override_type="boost"), PRESET_COMFORT),
        (_mixed_room(override_heat_temp=17.0, override_cool_temp=27.0, override_type="eco"), PRESET_ECO),
        (_mixed_room(override_heat_temp=20.0, override_cool_temp=25.0, override_type="custom"), PRESET_OVERRIDE),
        (_mixed_room(), PRESET_SCHEDULE),
        (_mixed_room(schedules=[]), None),
    ],
)
def test_room_climate_preset_mode_maps_roommind_state(mock_coordinator, room, expected):
    """Preset mode mirrors the active RoomMind override or schedule state."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = room
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.preset_mode == expected


@pytest.mark.asyncio
async def test_room_climate_set_temperature_range_stores_split_override(mock_coordinator):
    """Range updates store split override fields."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(schedules=[])
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_temperature(target_temp_low=20.0, target_temp_high=24.0)
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 20.0,
            "override_cool_temp": 24.0,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_room_climate_set_temperature_single_point(mock_coordinator):
    """Single target updates clear split override fields."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
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
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_comfort(mock_coordinator):
    """Comfort preset writes the stored comfort targets as a RoomMind boost override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_COMFORT)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 21.0,
            "override_cool_temp": 24.0,
            "override_until": None,
            "override_type": "boost",
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_eco(mock_coordinator):
    """Eco preset writes the stored eco targets as an eco override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_ECO)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 17.0,
            "override_cool_temp": 27.0,
            "override_until": None,
            "override_type": "eco",
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_override_freezes_current_targets(mock_coordinator):
    """Override preset freezes the current effective targets into a custom override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(schedules=[])
    coordinator.data = {
        "rooms": {"living_room": {"heat_target": 19.5, "cool_target": 25.5, "target_temp": 19.5}}
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_preset_mode(PRESET_OVERRIDE)

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


@pytest.mark.asyncio
async def test_room_climate_set_preset_mode_schedule_clears_override(mock_coordinator):
    """Schedule preset clears any active RoomMind override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room(
        override_heat_temp=21.0,
        override_cool_temp=24.0,
        override_type="boost",
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
async def test_room_climate_set_temperature_range_updates_comfort_preset(mock_coordinator):
    """Range edits under comfort update stored comfort temps and preserve the preset."""
    coordinator, store = mock_coordinator
    room = _mixed_room(
        override_heat_temp=21.0,
        override_cool_temp=24.0,
        override_type="boost",
    )
    store.get_room.return_value = room
    store.async_save_room.return_value = _mixed_room(
        comfort_heat=20.0,
        comfort_cool=25.0,
        override_heat_temp=21.0,
        override_cool_temp=24.0,
        override_type="boost",
    )
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(target_temp_low=20.0, target_temp_high=25.0)

    store.async_save_room.assert_awaited_once_with(
        "living_room",
        {"comfort_heat": 20.0, "comfort_cool": 25.0},
    )
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 20.0,
            "override_cool_temp": 25.0,
            "override_until": None,
            "override_type": "boost",
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_temperature_range_updates_eco_preset(mock_coordinator):
    """Range edits under eco update stored eco temps and preserve the preset."""
    coordinator, store = mock_coordinator
    room = _mixed_room(
        override_heat_temp=17.0,
        override_cool_temp=27.0,
        override_type="eco",
    )
    store.get_room.return_value = room
    store.async_save_room.return_value = _mixed_room(
        eco_heat=18.0,
        eco_cool=26.0,
        override_heat_temp=17.0,
        override_cool_temp=27.0,
        override_type="eco",
    )
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(target_temp_low=18.0, target_temp_high=26.0)

    store.async_save_room.assert_awaited_once_with(
        "living_room",
        {"eco_heat": 18.0, "eco_cool": 26.0},
    )
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 18.0,
            "override_cool_temp": 26.0,
            "override_until": None,
            "override_type": "eco",
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_temperature_override_preset_keeps_custom_override(mock_coordinator):
    """Single-point edits under override keep writing a custom override."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
        "override_temp": 21.0,
        "override_type": OVERRIDE_CUSTOM,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(temperature=22.5)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": 22.5,
            "override_heat_temp": None,
            "override_cool_temp": None,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )


@pytest.mark.asyncio
async def test_room_climate_set_temperature_schedule_preset_switches_to_override(mock_coordinator):
    """Editing targets on schedule creates a custom override immediately."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = _mixed_room()
    entity = RoomMindRoomClimate(coordinator, "living_room")

    await entity.async_set_temperature(target_temp_low=19.0, target_temp_high=24.0)

    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": None,
            "override_heat_temp": 19.0,
            "override_cool_temp": 24.0,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
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

@pytest.mark.asyncio
async def test_async_setup_entry_creates_entities_for_all_rooms():
    """async_setup_entry creates one climate entity per room."""
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
    assert len(entities) == 2
    assert all(isinstance(e, RoomMindRoomClimate) for e in entities)
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
