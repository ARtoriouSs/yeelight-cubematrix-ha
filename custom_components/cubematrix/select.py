"""The background preset of a cube"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .data import DATA_KEY, cube_device_info

if TYPE_CHECKING:
    from . import CubeMatrixConfigEntry

_LOGGER = logging.getLogger(__name__)

OFF = "off"
# Used for a new cube and when the chosen preset is gone. It is built in, so always there
DEFAULT_BACKGROUND = "fire"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CubeMatrixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([BackgroundSelect(entry)])


class BackgroundSelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "background"
    _attr_should_poll = False

    def __init__(self, entry: CubeMatrixConfigEntry) -> None:
        self.player = entry.runtime_data.player
        self._attr_unique_id = f"{entry.data[CONF_HOST]}_background"
        self._attr_device_info = cube_device_info(entry)
        self._attr_options = [OFF]

    @property
    def current_option(self) -> str:
        return self.player.background or OFF

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        data = self.hass.data[DATA_KEY]
        self._attr_options = [OFF, *data.library().names()]
        self.async_on_remove(self.player.add_listener(self.async_write_ha_state))
        self.async_on_remove(data.add_listener(self._presets_changed))
        last_state = await self.async_get_last_state()
        if last_state is not None:
            await self._set(last_state.state)

    async def async_select_option(self, option: str) -> None:
        await self.player.set_background(None if option == OFF else option)

    @callback
    def _presets_changed(self) -> None:
        self._attr_options = [OFF, *self.hass.data[DATA_KEY].library().names()]
        background = self.player.background
        if background is not None and background not in self._attr_options:
            _LOGGER.info("Background %s is gone, using %s", background, DEFAULT_BACKGROUND)
            self.hass.async_create_task(self.player.set_background(DEFAULT_BACKGROUND))
        self.async_write_ha_state()

    async def _set(self, option: str) -> None:
        """Use a restored option, or the default when it can't be used any more"""
        try:
            await self.player.set_background(None if option == OFF else option)
        except (KeyError, ValueError) as err:
            _LOGGER.info(
                "Background %s can't be restored (%s), using %s",
                option,
                err,
                DEFAULT_BACKGROUND,
            )
            await self.player.set_background(DEFAULT_BACKGROUND)
