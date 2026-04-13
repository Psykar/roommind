"""Sensor platform for RoomMind."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RoomMindCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Room target/mode state now lives on the unified climate entity."""
    coordinator: RoomMindCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_add_entities = async_add_entities
