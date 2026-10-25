"""Taps on the lamp's button as events"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import cube_device_info

if TYPE_CHECKING:
    from . import CubeMatrixConfigEntry

TAP = "tap"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CubeMatrixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ButtonEvent(entry)])


class ButtonEvent(EventEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "button"
    _attr_should_poll = False
    _attr_event_types = [TAP]

    def __init__(self, entry: CubeMatrixConfigEntry) -> None:
        self.connection = entry.runtime_data.connection
        self._attr_unique_id = f"{entry.data[CONF_HOST]}_button"
        self._attr_device_info = cube_device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.connection.on_button = self._tapped

        def forget() -> None:
            self.connection.on_button = None

        self.async_on_remove(forget)

    @callback
    def _tapped(self) -> None:
        self._trigger_event(TAP)
        self.async_write_ha_state()
