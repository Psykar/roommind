"""Tests for orphaned entity cleanup, history management."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import (
    _create_coordinator,
)


class TestCoverageGaps:
    """Tests covering uncovered coordinator lines."""

    def test_cleanup_orphaned_entities_removes_orphaned(self, hass, mock_config_entry):
        """cleanup_orphaned_entities removes entities for deleted rooms."""
        from custom_components.roommind.const import DOMAIN

        coordinator = _create_coordinator(hass, mock_config_entry)

        store = MagicMock()
        store.get_rooms.return_value = {"living_room": {"covers": ["cover.x"]}}
        hass.data = {DOMAIN: {"store": store}}

        # Simulate entity registry entries
        entry_valid_climate = MagicMock()
        entry_valid_climate.unique_id = f"{DOMAIN}_living_room_climate"
        entry_valid_climate.entity_id = "climate.roommind_living_room"

        entry_valid_cover_auto = MagicMock()
        entry_valid_cover_auto.unique_id = f"{DOMAIN}_living_room_cover_auto"
        entry_valid_cover_auto.entity_id = "switch.roommind_living_room_cover_auto"

        entry_valid_cover_paused = MagicMock()
        entry_valid_cover_paused.unique_id = f"{DOMAIN}_living_room_cover_paused"
        entry_valid_cover_paused.entity_id = "binary_sensor.roommind_living_room_cover_paused"

        # Orphaned: room no longer exists
        entry_orphaned_room = MagicMock()
        entry_orphaned_room.unique_id = f"{DOMAIN}_deleted_room_climate"
        entry_orphaned_room.entity_id = "climate.roommind_deleted_room"

        # Non-roommind entity -- should be ignored
        entry_other = MagicMock()
        entry_other.unique_id = "other_integration_something"
        entry_other.entity_id = "sensor.other_thing"

        mock_registry = MagicMock()
        # Global entity (not per-room) -- should be kept
        entry_vacation = MagicMock()
        entry_vacation.unique_id = f"{DOMAIN}_vacation"
        entry_vacation.entity_id = "switch.roommind_vacation"

        mock_registry.entities.values.return_value = [
            entry_valid_climate,
            entry_valid_cover_auto,
            entry_valid_cover_paused,
            entry_orphaned_room,
            entry_other,
            entry_vacation,
        ]

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_registry,
        ):
            coordinator.cleanup_orphaned_entities()

        # Only the orphaned entity should be removed (vacation is global, not orphaned)
        mock_registry.async_remove.assert_called_once_with("climate.roommind_deleted_room")

    def test_cleanup_orphaned_entities_removes_cover_entities_without_covers(self, hass, mock_config_entry):
        """cleanup_orphaned_entities removes cover entities when room has no covers configured."""
        from custom_components.roommind.const import DOMAIN

        coordinator = _create_coordinator(hass, mock_config_entry)

        store = MagicMock()
        # Room exists but has no covers
        store.get_rooms.return_value = {"living_room": {}}
        hass.data = {DOMAIN: {"store": store}}

        entry_cover_auto = MagicMock()
        entry_cover_auto.unique_id = f"{DOMAIN}_living_room_cover_auto"
        entry_cover_auto.entity_id = "switch.roommind_living_room_cover_auto"

        entry_cover_paused = MagicMock()
        entry_cover_paused.unique_id = f"{DOMAIN}_living_room_cover_paused"
        entry_cover_paused.entity_id = "binary_sensor.roommind_living_room_cover_paused"

        entry_valid = MagicMock()
        entry_valid.unique_id = f"{DOMAIN}_living_room_climate"
        entry_valid.entity_id = "climate.roommind_living_room"

        mock_registry = MagicMock()
        mock_registry.entities.values.return_value = [
            entry_cover_auto,
            entry_cover_paused,
            entry_valid,
        ]

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_registry,
        ):
            coordinator.cleanup_orphaned_entities()

        # Cover entities removed (room has no covers), climate kept
        removed_ids = [c.args[0] for c in mock_registry.async_remove.call_args_list]
        assert "switch.roommind_living_room_cover_auto" in removed_ids
        assert "binary_sensor.roommind_living_room_cover_paused" in removed_ids
        assert "climate.roommind_living_room" not in removed_ids

    def test_cleanup_orphaned_entities_no_orphans(self, hass, mock_config_entry):
        """cleanup_orphaned_entities does nothing when all entities are valid."""
        from custom_components.roommind.const import DOMAIN

        coordinator = _create_coordinator(hass, mock_config_entry)

        store = MagicMock()
        store.get_rooms.return_value = {"living_room": {"covers": ["cover.x"]}}
        hass.data = {DOMAIN: {"store": store}}

        entry_valid = MagicMock()
        entry_valid.unique_id = f"{DOMAIN}_living_room_climate"
        entry_valid.entity_id = "climate.roommind_living_room"

        mock_registry = MagicMock()
        mock_registry.entities.values.return_value = [entry_valid]

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_registry,
        ):
            coordinator.cleanup_orphaned_entities()

        mock_registry.async_remove.assert_not_called()

    def test_cleanup_orphaned_entities_skips_non_string_unique_id(self, hass, mock_config_entry):
        """cleanup_orphaned_entities skips entities with non-string unique_id (e.g. int)."""
        from custom_components.roommind.const import DOMAIN

        coordinator = _create_coordinator(hass, mock_config_entry)

        store = MagicMock()
        store.get_rooms.return_value = {"living_room": {}}
        hass.data = {DOMAIN: {"store": store}}

        entry_int_uid = MagicMock()
        entry_int_uid.unique_id = 12345
        entry_int_uid.entity_id = "sensor.some_other_integration"

        entry_none_uid = MagicMock()
        entry_none_uid.unique_id = None
        entry_none_uid.entity_id = "sensor.no_uid"

        mock_registry = MagicMock()
        mock_registry.entities.values.return_value = [entry_int_uid, entry_none_uid]

        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_registry,
        ):
            coordinator.cleanup_orphaned_entities()

        mock_registry.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_room_removed_cleans_history_store(self, hass, mock_config_entry):
        """async_room_removed calls history_store.remove_room."""
        coordinator = _create_coordinator(hass, mock_config_entry)
        coordinator.async_request_refresh = AsyncMock()

        mock_history_store = MagicMock()
        mock_history_store.remove_room = MagicMock()
        coordinator._history_store = mock_history_store

        mock_registry = MagicMock()
        mock_registry.entities = MagicMock()
        mock_registry.entities.values.return_value = []
        with patch(
            "homeassistant.helpers.entity_registry.async_get",
            return_value=mock_registry,
        ):
            await coordinator.async_room_removed("test_room")

        mock_history_store.remove_room.assert_called_once_with("test_room")
