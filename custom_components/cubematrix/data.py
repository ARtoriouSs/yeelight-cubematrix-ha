"""Presets shared by all cubes: stored ones, files and the built-in set"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .artformat import Art, ArtError, parse_text
from .const import DOMAIN, PRESET_DIR, STORE_KEY, STORE_VERSION
from .presets import Playable, PresetLibrary, load_builtin, load_dir

if TYPE_CHECKING:
    from .connection import CubeConnection
    from .player import Player

_LOGGER = logging.getLogger(__name__)

DATA_KEY: HassKey[CubeMatrixData] = HassKey(DOMAIN)


@dataclass
class CubeRuntime:
    """What one config entry keeps while it is loaded"""

    connection: CubeConnection
    player: Player


def cube_device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device per cube, all its entities share it"""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.data[CONF_HOST])},
        manufacturer="Yeelight",
        model="Cube Matrix",
        name=entry.title,
    )


def parse_preset(name: str, text: str) -> Art:
    """Check a preset text, it must hold exactly one art. The name given here wins"""
    arts = parse_text(text)
    if len(arts) != 1:
        raise ArtError(f"a preset must hold exactly one art, found {len(arts)}")
    return dataclasses.replace(arts[0], name=name)


class CubeMatrixData:
    """Lives in hass.data[DOMAIN], made once in async_setup"""

    def __init__(
        self,
        hass: HomeAssistant,
        store: Store[dict[str, Any]],
        builtin: dict[str, Playable],
        preset_dir: Path,
    ) -> None:
        self.hass = hass
        self.store = store
        self.stored: dict[str, str] = {}
        self.files: dict[str, Art] = {}
        self.file_errors: list[str] = []
        self._builtin = builtin
        self._preset_dir = preset_dir
        self._stored_arts: dict[str, Art] = {}
        self._listeners: list[Callable[[], None]] = []

    @classmethod
    async def async_load(cls, hass: HomeAssistant) -> CubeMatrixData:
        store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        builtin = await hass.async_add_executor_job(load_builtin)
        data = cls(hass, store, builtin, Path(hass.config.path(PRESET_DIR)))
        saved = await store.async_load() or {}
        for name, text in (saved.get("presets") or {}).items():
            data.stored[name] = text
            try:
                data._stored_arts[name] = parse_preset(name, text)
            except ArtError as err:
                # Kept, so the owner can fix it in the options form
                _LOGGER.warning("Saved preset %s is invalid: %s", name, err)
        await data._load_files()
        return data

    def library(self) -> PresetLibrary:
        return PresetLibrary(self._builtin, self.files, self._stored_arts)

    async def save_preset(self, name: str, text: str) -> None:
        """Validate and save, raises ArtError and saves nothing on a bad text"""
        art = parse_preset(name, text)
        self.stored[name] = text
        self._stored_arts[name] = art
        await self._save()
        self._notify()

    async def delete_preset(self, name: str) -> None:
        if name not in self.stored:
            return
        del self.stored[name]
        self._stored_arts.pop(name, None)
        await self._save()
        self._notify()

    async def reload_files(self) -> None:
        await self._load_files()
        self._notify()

    @callback
    def add_listener(self, callback_: Callable[[], None]) -> Callable[[], None]:
        """Call back when presets change, returns a function to stop it"""
        self._listeners.append(callback_)

        def remove() -> None:
            if callback_ in self._listeners:
                self._listeners.remove(callback_)

        return remove

    async def _load_files(self) -> None:
        files, errors = await self.hass.async_add_executor_job(
            load_dir, self._preset_dir
        )
        for error in errors:
            _LOGGER.warning("Preset file skipped: %s", error)
        self.files = files
        self.file_errors = errors

    async def _save(self) -> None:
        await self.store.async_save({"presets": dict(self.stored)})

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error in a preset listener")
