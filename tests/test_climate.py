"""Tests for RoomMind climate platform."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import PRESET_BOOST, PRESET_COMFORT, PRESET_ECO, HVACAction, HVACMode

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
    OVERRIDE_BOOST,
    OVERRIDE_CUSTOM,
    OVERRIDE_ECO,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    store = MagicMock()
    coordinator.hass.data = {DOMAIN: {"store": store}}
    coordinator.data = {}
    return coordinator, store


def test_create_room_climates(mock_coordinator):
    """Factory creates exactly one climate entity per room."""
    coordinator, _ = mock_coordinator
    climates = _create_room_climates(coordinator, "living_room")
    assert len(climates) == 1
    assert isinstance(climates[0], RoomMindRoomClimate)


def test_unique_id_and_entity_id(mock_coordinator):
    """Climate entity has correct unique_id and entity_id."""
    coordinator, _ = mock_coordinator
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.unique_id == "roommind_living_room_climate"
    assert entity.entity_id == "climate.roommind_living_room"


def test_hvac_modes_include_heat_cool_for_mixed_room(mock_coordinator):
    """Auto rooms with TRV + AC expose full heat/cool/off controls."""
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


def test_hvac_mode_off_when_climate_control_disabled(mock_coordinator):
    """Disabled room climate control surfaces as HVAC OFF."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": False,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_mode == HVACMode.OFF
    assert entity.hvac_action == HVACAction.OFF


def test_hvac_mode_heat_for_heat_only_room(mock_coordinator):
    """Heat-only rooms map to HVAC HEAT."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.HEAT]


def test_hvac_mode_cool_for_cool_only_room(mock_coordinator):
    """Cool-only rooms map to HVAC COOL."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.ac", "type": "ac"}],
        "climate_mode": CLIMATE_MODE_COOL_ONLY,
        "climate_control_enabled": True,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_mode == HVACMode.COOL
    assert entity.hvac_modes == [HVACMode.OFF, HVACMode.COOL]


def test_target_temperature_prefers_live_target(mock_coordinator):
    """Target temperature reflects the effective live room target."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "override_temp": 23.5,
        "override_until": None,
        "override_type": "custom",
    }
    coordinator.data = {"rooms": {"living_room": {"target_temp": 21.5}}}
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.target_temperature == 21.5


def test_target_temperature_returns_default_when_no_override(mock_coordinator):
    """target_temperature returns DEFAULT_COMFORT_TEMP when no live/override target exists."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "override_temp": None,
        "override_until": None,
        "override_type": None,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.target_temperature == DEFAULT_COMFORT_TEMP


def test_current_temperature_from_coordinator_data(mock_coordinator):
    """current_temperature reads from coordinator.data."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {"override_temp": None}
    coordinator.data = {"rooms": {"living_room": {"current_temp": 20.5}}}
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.current_temperature == 20.5


def test_current_temperature_none_when_no_data(mock_coordinator):
    """current_temperature returns None when coordinator has no data."""
    coordinator, store = mock_coordinator
    coordinator.data = None
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.current_temperature is None


def test_current_temperature_none_when_room_not_in_data(mock_coordinator):
    """current_temperature returns None when room not in coordinator data."""
    coordinator, store = mock_coordinator
    coordinator.data = {"rooms": {"other_room": {"current_temp": 20.0}}}
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.current_temperature is None


def test_hvac_action_maps_room_mode(mock_coordinator):
    """Live room modes map cleanly to HVAC actions."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": True,
    }
    coordinator.data = {"rooms": {"living_room": {"mode": "heating"}}}
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.hvac_action == HVACAction.HEATING


def test_preset_mode_reports_eco(mock_coordinator):
    """Eco overrides surface as the ECO preset."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "override_temp": 17.0,
        "override_until": time.time() + 3600,
        "override_type": OVERRIDE_ECO,
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.preset_mode == PRESET_ECO


def test_preset_mode_reports_boost(mock_coordinator):
    """Boost overrides surface as the BOOST preset."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "override_temp": 21.0,
        "override_until": time.time() + 3600,
        "override_type": OVERRIDE_BOOST,
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.preset_mode == PRESET_BOOST


def test_preset_mode_reports_comfort_from_matching_custom_override(mock_coordinator):
    """A custom override at comfort temp is surfaced as COMFORT."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = {
        "override_temp": 21.0,
        "override_until": None,
        "override_type": OVERRIDE_CUSTOM,
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "comfort_heat": 21.0,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    assert entity.preset_mode == PRESET_COMFORT


@pytest.mark.asyncio
async def test_set_temperature(mock_coordinator):
    """set_temperature activates a permanent custom override and refreshes."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_temperature(temperature=22.0)
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "override_temp": 22.0,
            "override_until": None,
            "override_type": OVERRIDE_CUSTOM,
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_temperature_no_temp_kwarg(mock_coordinator):
    """set_temperature does nothing when temperature kwarg is missing."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_temperature()
    store.async_update_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_hvac_mode_off_disables_climate_control(mock_coordinator):
    """Setting HVAC OFF disables climate control."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_hvac_mode(HVACMode.OFF)
    store.async_update_room.assert_awaited_once_with("living_room", {"climate_control_enabled": False})
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_hvac_mode_heat_updates_room_mode(mock_coordinator):
    """Setting HVAC HEAT enables control and sets heat-only mode."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_hvac_mode(HVACMode.HEAT)
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "climate_control_enabled": True,
            "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_hvac_mode_heat_cool_updates_room_mode(mock_coordinator):
    """Setting HVAC HEAT_COOL restores auto room mode."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "climate_control_enabled": True,
            "climate_mode": CLIMATE_MODE_AUTO,
        },
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_preset_mode_eco(mock_coordinator):
    """Eco preset persists an eco override."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    store.get_room.return_value = {
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "eco_heat": 17.0,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_preset_mode(PRESET_ECO)
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {"override_temp": 17.0, "override_until": None, "override_type": OVERRIDE_ECO},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_preset_mode_comfort(mock_coordinator):
    """Comfort preset persists a custom override at comfort temp."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    store.get_room.return_value = {
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "comfort_heat": 22.0,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_set_preset_mode(PRESET_COMFORT)
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {"override_temp": 22.0, "override_until": None, "override_type": OVERRIDE_CUSTOM},
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_on_restores_supported_mode(mock_coordinator):
    """Turning on restores the first non-off HVAC mode."""
    coordinator, store = mock_coordinator
    store.async_update_room = AsyncMock()
    store.get_room.return_value = {
        "devices": [{"entity_id": "climate.trv", "type": "trv"}],
        "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        "climate_control_enabled": False,
    }
    entity = RoomMindRoomClimate(coordinator, "living_room")
    await entity.async_turn_on()
    store.async_update_room.assert_awaited_once_with(
        "living_room",
        {
            "climate_control_enabled": True,
            "climate_mode": CLIMATE_MODE_HEAT_ONLY,
        },
    )


def test_hvac_mode_off_when_room_missing(mock_coordinator):
    """Missing rooms surface as HVAC OFF."""
    coordinator, store = mock_coordinator
    store.get_room.return_value = None
    entity = RoomMindRoomClimate(coordinator, "nonexistent")
    assert entity.hvac_mode == HVACMode.OFF


@pytest.mark.asyncio
async def test_async_setup_entry_creates_entities_for_all_rooms():
    """async_setup_entry creates climate entities for all rooms."""
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
