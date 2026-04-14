"""Helpers for building human-friendly RoomMind entity names."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar


def get_area_name(hass: HomeAssistant, area_id: str) -> str:
    """Return the Home Assistant area name for an area id when available."""
    try:
        area_reg = ar.async_get(hass)
        area = area_reg.async_get_area(area_id)
        return area.name if area else area_id
    except Exception:  # noqa: BLE001
        return area_id
