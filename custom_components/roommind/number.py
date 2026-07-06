"""Number platform for RoomMind."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
    DOMAIN,
    MAX_CLIMATE_OVERRIDE_TIMEOUT_MINUTES,
    SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES,
)
from .coordinator import RoomMindCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RoomMind number entities from a config entry."""
    coordinator: RoomMindCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoomMindDefaultOverrideTimeoutNumber(coordinator)])


class RoomMindDefaultOverrideTimeoutNumber(CoordinatorEntity, NumberEntity):
    """Global number for default climate override timeout."""

    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_default_override_timeout"
    _attr_name = "Default Override Timeout"
    _attr_icon = "mdi:timer-cog-outline"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_CLIMATE_OVERRIDE_TIMEOUT_MINUTES
    _attr_native_step = 15
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: RoomMindCoordinator) -> None:
        super().__init__(coordinator)
        self.entity_id = f"number.{DOMAIN}_default_override_timeout"

    @property
    def native_value(self) -> float:
        """Return the stored default timeout in minutes."""
        store = self.coordinator.hass.data[DOMAIN]["store"]
        settings = store.get_settings()
        value = settings.get(SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES, DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES)
        if not isinstance(value, (int, float)):
            return float(DEFAULT_CLIMATE_OVERRIDE_TIMEOUT_MINUTES)
        return float(max(0, min(int(value), MAX_CLIMATE_OVERRIDE_TIMEOUT_MINUTES)))

    async def async_set_native_value(self, value: float) -> None:
        """Persist the default timeout in minutes."""
        minutes = max(0, min(int(round(value)), MAX_CLIMATE_OVERRIDE_TIMEOUT_MINUTES))
        store = self.coordinator.hass.data[DOMAIN]["store"]
        await store.async_save_settings({SETTING_DEFAULT_OVERRIDE_TIMEOUT_MINUTES: minutes})
        await self.coordinator.async_request_refresh()
