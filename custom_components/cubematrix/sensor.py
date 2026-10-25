"""What the cube shows right now"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import cube_device_info

if TYPE_CHECKING:
    from . import CubeMatrixConfigEntry

OFF = "off"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CubeMatrixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PlayingSensor(entry)])


class PlayingSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "playing"
    _attr_should_poll = False

    def __init__(self, entry: CubeMatrixConfigEntry) -> None:
        self.player = entry.runtime_data.player
        self._attr_unique_id = f"{entry.data[CONF_HOST]}_playing"
        self._attr_device_info = cube_device_info(entry)

    @property
    def native_value(self) -> str:
        return self.player.playing or OFF

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.player.add_listener(self.async_write_ha_state))
