"""Tests for override/vacation/presence/schedule priority chain."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_OVERRIDE,
    PRESET_SCHEDULE,
    PRESET_VACATION,
)

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    _presence_states_get,
    make_mock_states_get,
)


class TestRoomMindCoordinator:
    """Tests for RoomMindCoordinator."""

    @pytest.mark.asyncio
    async def test_override_takes_priority_over_schedule(self, hass, mock_config_entry):
        """Test that an active override overrides the schedule target temp."""
        room_with_override = {
            **SAMPLE_ROOM,
            "override_temp": 25.0,
            "override_until": time.time() + 3600,
            "override_type": "boost",
        }
        store = _make_store_mock({"living_room_abc12345": room_with_override})
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=make_mock_states_get(schedule_state="off"))
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 25.0
        assert room_state["override_active"] is True
        assert room_state["override_type"] == "boost"
        assert room_state["climate"]["preset_mode"] == PRESET_COMFORT
        assert room_state["climate"]["active_source"] == "comfort_hold"

    @pytest.mark.asyncio
    async def test_expired_override_falls_back_to_schedule(self, hass, mock_config_entry):
        """Test that an expired override reverts to normal schedule logic."""
        room_with_expired = {
            **SAMPLE_ROOM,
            "override_temp": 25.0,
            "override_until": time.time() - 10,
            "override_type": "boost",
        }
        store = _make_store_mock({"living_room_abc12345": room_with_expired})
        store.async_update_room = AsyncMock()
        hass.data = {"roommind": {"store": store}}
        hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

        hass.states.get = MagicMock(side_effect=make_mock_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 21.0
        assert room_state["override_active"] is False

    @pytest.mark.asyncio
    async def test_permanent_override_takes_priority(self, hass, mock_config_entry):
        """Permanent override (override_until=None) takes priority over schedule."""
        room_with_override = {
            **SAMPLE_ROOM,
            "override_temp": 23.0,
            "override_until": None,
            "override_type": "custom",
        }
        store = _make_store_mock({"living_room_abc12345": room_with_override})
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=make_mock_states_get(schedule_state="off"))
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 23.0
        assert room_state["override_active"] is True
        assert room_state["override_type"] == "custom"

    @pytest.mark.asyncio
    async def test_split_override_sets_heat_and_cool_targets(self, hass, mock_config_entry):
        """Split overrides drive live targets and still count as active overrides."""
        room_with_override = {
            **SAMPLE_ROOM,
            "override_heat_temp": 20.0,
            "override_cool_temp": 24.0,
            "override_until": time.time() + 3600,
            "override_type": "custom",
        }
        store = _make_store_mock({"living_room_abc12345": room_with_override})
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=make_mock_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 20.0
        assert room_state["heat_target"] == 20.0
        assert room_state["cool_target"] == 24.0
        assert room_state["override_active"] is True
        assert room_state["climate"]["preset_mode"] == PRESET_OVERRIDE
        assert room_state["climate"]["active_source"] == "manual_hold"


class TestVacationMode:
    """Tests for vacation mode target temperature override."""

    @pytest.mark.asyncio
    async def test_vacation_overrides_schedule(self, hass, mock_config_entry):
        """Active vacation mode uses vacation_temp instead of schedule."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "vacation_temp": 15.0,
            "vacation_until": time.time() + 86400,
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=make_mock_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 15.0
        assert room_state["climate"]["active_source"] == "vacation"
        assert room_state["climate"]["preset_mode"] == PRESET_VACATION

    @pytest.mark.asyncio
    async def test_override_beats_vacation(self, hass, mock_config_entry):
        """Manual override takes priority over vacation mode."""
        room_with_override = {
            **SAMPLE_ROOM,
            "override_temp": 25.0,
            "override_until": time.time() + 3600,
            "override_type": "boost",
        }
        store = _make_store_mock({"living_room_abc12345": room_with_override})
        store.get_settings.return_value = {
            "vacation_temp": 15.0,
            "vacation_until": time.time() + 86400,
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=make_mock_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 25.0

    @pytest.mark.asyncio
    async def test_expired_vacation_falls_back_to_schedule(self, hass, mock_config_entry):
        """Expired vacation mode reverts to normal schedule logic."""
        store = _make_store_mock({"living_room_abc12345": SAMPLE_ROOM})
        store.get_settings.return_value = {
            "vacation_temp": 15.0,
            "vacation_until": time.time() - 10,
        }
        store.async_save_settings = AsyncMock()
        hass.data = {"roommind": {"store": store}}
        hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

        hass.states.get = MagicMock(side_effect=make_mock_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 21.0  # comfort_temp from schedule
        assert room_state["climate"]["preset_mode"] == PRESET_SCHEDULE

    @pytest.mark.asyncio
    async def test_vacation_cool_target_stays_at_eco_cool(self, hass, mock_config_entry):
        """Vacation should not collapse cool_target to vacation_temp."""
        room = {**SAMPLE_ROOM, "eco_cool": 27.0}
        store = _make_store_mock({"living_room_abc12345": room})
        store.get_settings.return_value = {
            "vacation_temp": 15.0,
            "vacation_until": time.time() + 86400,
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=make_mock_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["heat_target"] == 15.0
        assert room_state["cool_target"] == 27.0  # eco_cool, not 15
        assert room_state["climate"]["preset_mode"] == PRESET_VACATION


class TestPresenceAwayMode:
    """Tests for presence-away target resolution."""

    @pytest.mark.asyncio
    async def test_presence_away_sets_read_only_preset(self, hass, mock_config_entry):
        """Presence-away resolution surfaces the away preset on the room climate."""
        room = {**SAMPLE_ROOM, "occupancy_sensors": ["person.alice"]}
        store = _make_store_mock({"living_room_abc12345": room})
        store.get_settings.return_value = {
            "presence_enabled": True,
            "presence_persons": ["person.alice"],
            "presence_away_action": "eco",
        }
        hass.data = {"roommind": {"store": store}}

        hass.states.get = MagicMock(side_effect=_presence_states_get())
        hass.services.async_call = AsyncMock()

        coordinator = _create_coordinator(hass, mock_config_entry)
        data = await coordinator._async_update_data()

        room_state = data["rooms"]["living_room_abc12345"]
        assert room_state["target_temp"] == 17.0
        assert room_state["climate"]["active_source"] == "presence_away"
        assert room_state["climate"]["preset_mode"] == PRESET_AWAY
