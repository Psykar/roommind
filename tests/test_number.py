"""Tests for the number platform."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.const import (
    DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
    DOMAIN,
    SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES,
)
from custom_components.roommind.number import RoomMindDefaultOverrideTimeoutNumber, async_setup_entry


@pytest.mark.asyncio
async def test_setup_entry_creates_default_override_timeout_number(hass, mock_config_entry, store):
    """Number setup creates the global default override timeout entity."""
    await store.async_load()
    coordinator = MagicMock()
    hass.data[DOMAIN] = {mock_config_entry.entry_id: coordinator, "store": store}
    add_entities = MagicMock()

    await async_setup_entry(hass, mock_config_entry, add_entities)

    add_entities.assert_called_once()
    entities = add_entities.call_args[0][0]
    assert len(entities) == 1
    assert isinstance(entities[0], RoomMindDefaultOverrideTimeoutNumber)


def test_number_reads_default_value_from_settings():
    """Number returns stored timeout or the integration default."""
    coordinator = MagicMock()
    store = MagicMock()
    store.get_settings.return_value = {}
    coordinator.hass.data = {DOMAIN: {"store": store}}
    entity = RoomMindDefaultOverrideTimeoutNumber(coordinator)

    assert entity.native_value == float(DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES)

    store.get_settings.return_value = {SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES: 45}
    assert entity.native_value == 45.0


@pytest.mark.asyncio
async def test_number_persists_timeout_changes():
    """Setting the number saves settings and requests a refresh."""
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    store = MagicMock()
    store.async_save_settings = AsyncMock()
    store.get_settings.return_value = {}
    coordinator.hass.data = {DOMAIN: {"store": store}}
    entity = RoomMindDefaultOverrideTimeoutNumber(coordinator)

    await entity.async_set_native_value(90)

    store.async_save_settings.assert_awaited_once_with({SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES: 90})
    coordinator.async_request_refresh.assert_awaited_once()
