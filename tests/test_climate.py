"""Tests for RoomMind climate platform."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode

from custom_components.roommind import climate as climate_module
from custom_components.roommind.climate import (
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
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.hass.states.get = MagicMock(return_value=None)
    store = MagicMock()
    store.get_settings.return_value = {}
    coordinator.hass.data = {DOMAIN: {"store": store}}
    coordinator.data = {}
    return coordinator, store


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


@pytest.mark.asyncio
async def test_room_climate_set_temperature_range_stores_split_override(mock_coordinator):
    """Range updates store split override fields."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    store.get_room.return_value = {
        "devices": [
            {"entity_id": "climate.trv", "type": "trv"},
            {"entity_id": "climate.ac", "type": "ac"},
        ],
        "climate_mode": CLIMATE_MODE_AUTO,
        "climate_control_enabled": True,
    }
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
    store.async_update_room = AsyncMock()
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
async def test_room_climate_set_hvac_mode_updates_room_mode(mock_coordinator):
    """Room climate HVAC writes update climate_control_enabled and climate_mode."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
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
