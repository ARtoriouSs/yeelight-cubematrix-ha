"""The cube as a light: power and brightness"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.const import CONF_HOST, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)

from .data import cube_device_info
from .protocol import LampError

if TYPE_CHECKING:
    from . import CubeMatrixConfigEntry

# A show with wait: true must not block other calls to the same light
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CubeMatrixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([CubeLight(entry)])


class CubeLight(LightEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entry: CubeMatrixConfigEntry) -> None:
        self.runtime = entry.runtime_data
        self.player = entry.runtime_data.player
        self._attr_unique_id = entry.data[CONF_HOST]
        self._attr_device_info = cube_device_info(entry)
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool:
        return self.player.power

    @property
    def brightness(self) -> int:
        return self.player.brightness

    @property
    def available(self) -> bool:
        # The lamp is only asked while the light is on, so an off light stays
        # available, or it could never be turned on again
        return self.runtime.connection.available or not self.player.power

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        # The state has no brightness while off, so it is kept here too
        return RestoredExtraData({"brightness": self.player.brightness})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.player.add_listener(self.async_write_ha_state))
        connection = self.runtime.connection
        connection.on_change = self.async_write_ha_state

        def forget() -> None:
            connection.on_change = None

        self.async_on_remove(forget)

        last_state = await self.async_get_last_state()
        brightness = None
        if last_state is not None:
            brightness = last_state.attributes.get(ATTR_BRIGHTNESS)
        last_extra = await self.async_get_last_extra_data()
        if brightness is None and last_extra is not None:
            brightness = last_extra.as_dict().get("brightness")
        if isinstance(brightness, int):
            await self.player.set_brightness(brightness)
        # A new light starts on. Anything but off (for example unavailable) means on
        if last_state is None or last_state.state != STATE_OFF:
            await self.player.turn_on()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            await self.player.set_brightness(kwargs[ATTR_BRIGHTNESS])
        await self.player.turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.player.turn_off()
        except LampError as err:
            raise HomeAssistantError(f"Could not turn off the lamp: {err}") from err
