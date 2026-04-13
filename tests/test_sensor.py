"""Tests for the sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import DOMAIN
from custom_components.roommind.sensor import async_setup_entry


def _make_coordinator():
    coordinator = MagicMock()
    coordinator.data = {"rooms": {}}
    return coordinator


@pytest.mark.asyncio
async def test_setup_entry_registers_callback_only(hass, mock_config_entry, store):
    """Per-room target/mode sensors are replaced by the unified climate entity."""
    await store.async_load()
    await store.async_save_room("room_a", {"thermostats": ["climate.trv1"]})

    coordinator = _make_coordinator()
    hass.data[DOMAIN] = {
        mock_config_entry.entry_id: coordinator,
        "store": store,
    }
    add_entities = MagicMock()

    await async_setup_entry(hass, mock_config_entry, add_entities)

    assert coordinator.async_add_entities is add_entities
    add_entities.assert_not_called()
