"""Actions: show, set_background, stop (on cube lights) and reload_presets"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_platform_entity_service

from .artformat import Art, ArtError, parse_art, parse_text
from .const import DOMAIN
from .data import DATA_KEY
from .presets import Playable

if TYPE_CHECKING:
    from .light import CubeLight

ATTR_PRESET = "preset"
ATTR_DEFINITION = "definition"
ATTR_REPEAT = "repeat"
ATTR_DURATION = "duration"
ATTR_FPS = "fps"
ATTR_THEN = "then"
ATTR_FIT = "fit"
ATTR_WAIT = "wait"
ATTR_WAKE = "wake"

FOREVER = "forever"
FIT_MODES = ["error", "center", "tile"]
OFF = "off"

_POSITIVE = vol.All(vol.Coerce(float), vol.Range(min=0, min_included=False))

SHOW_SCHEMA = {
    vol.Optional(ATTR_PRESET): cv.string,
    vol.Optional(ATTR_DEFINITION): vol.Any(cv.string, dict),
    vol.Optional(ATTR_REPEAT, default=1): vol.Any(
        FOREVER, vol.All(vol.Coerce(int), vol.Range(min=1))
    ),
    vol.Optional(ATTR_DURATION): _POSITIVE,
    vol.Optional(ATTR_FPS): _POSITIVE,
    vol.Optional(ATTR_THEN, default="restore"): cv.string,
    vol.Optional(ATTR_FIT, default="error"): vol.In(FIT_MODES),
    vol.Optional(ATTR_WAIT, default=False): cv.boolean,
    vol.Optional(ATTR_WAKE, default=False): cv.boolean,
}
SET_BACKGROUND_SCHEMA = {vol.Required(ATTR_PRESET): cv.string}


def _art_from_definition(definition: str | Mapping[str, Any]) -> Art:
    if isinstance(definition, Mapping):
        return parse_art(definition)
    arts = parse_text(definition)
    if len(arts) != 1:
        raise ArtError(f"a definition must hold exactly one art, found {len(arts)}")
    return arts[0]


def _playable(entity: CubeLight, data: Mapping[str, Any]) -> tuple[str, Playable]:
    preset = data.get(ATTR_PRESET)
    definition = data.get(ATTR_DEFINITION)
    if (preset is None) == (definition is None):
        raise ServiceValidationError("Give either preset or definition, not both or none")
    if definition is not None:
        art = _art_from_definition(definition)
        return art.name, art
    playable = entity.hass.data[DATA_KEY].library().get(preset)
    if playable is None:
        raise ServiceValidationError(f"Unknown preset: {preset}")
    return preset, playable


async def _show(entity: CubeLight, call: ServiceCall) -> None:
    data = call.data
    repeat = data[ATTR_REPEAT]
    try:
        name, playable = _playable(entity, data)
        done = await entity.player.show(
            playable,
            name=name,
            repeat=None if repeat == FOREVER else repeat,
            duration=data.get(ATTR_DURATION),
            fps=data.get(ATTR_FPS),
            then=data[ATTR_THEN],
            fit=data[ATTR_FIT],
            wake=data[ATTR_WAKE],
        )
    except ArtError as err:
        raise ServiceValidationError(str(err)) from err
    except KeyError as err:
        # An unknown preset name in then
        raise ServiceValidationError(f"Unknown preset: {err.args[0]}") from err
    if data[ATTR_WAIT]:
        await done


async def _set_background(entity: CubeLight, call: ServiceCall) -> None:
    preset = call.data[ATTR_PRESET]
    try:
        await entity.player.set_background(None if preset == OFF else preset)
    except ArtError as err:
        raise ServiceValidationError(str(err)) from err
    except KeyError as err:
        raise ServiceValidationError(f"Unknown preset: {preset}") from err


async def _stop(entity: CubeLight, call: ServiceCall) -> None:
    await entity.player.stop()


def async_setup_services(hass: HomeAssistant) -> None:
    for name, schema, func in (
        ("show", SHOW_SCHEMA, _show),
        ("set_background", SET_BACKGROUND_SCHEMA, _set_background),
        ("stop", {}, _stop),
    ):
        async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=LIGHT_DOMAIN,
            schema=schema,
            func=func,
        )

    async def reload_presets(call: ServiceCall) -> None:
        await hass.data[DATA_KEY].reload_files()

    hass.services.async_register(DOMAIN, "reload_presets", reload_presets)
