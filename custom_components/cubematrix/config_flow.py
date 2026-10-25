"""Config flow (lamp address) and options flow (layout, presets)"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .artformat import ArtError
from .const import (
    CONF_DIRECTION,
    CONF_MODULES,
    DEFAULT_DIRECTION,
    DEFAULT_MODULES,
    DIRECTIONS,
    DOMAIN,
    MODEL,
)
from .data import DATA_KEY, CubeMatrixData
from .layout import parse_modules
from .protocol import LampError, get_props

CONF_NAME = "name"
CONF_TEXT = "text"


async def _read_model(host: str) -> str:
    result = await get_props(host, ["model"])
    return str(result[0]) if result else ""


class CubeMatrixConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            # The owner reserves the lamp IP in DHCP, so the IP names the lamp
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            try:
                model = await _read_model(host)
            except LampError:
                errors["base"] = "cannot_connect"
            else:
                if model.lower() != MODEL:
                    errors["base"] = "wrong_model"
                else:
                    return self.async_create_entry(
                        title=f"Cube Matrix ({host})", data={CONF_HOST: host}
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CubeMatrixOptionsFlow:
        return CubeMatrixOptionsFlow()


class CubeMatrixOptionsFlow(OptionsFlowWithReload):
    """A menu. Layout and port changes reload the entry, presets don't"""

    def __init__(self) -> None:
        self._edit_name: str | None = None

    @property
    def _data(self) -> CubeMatrixData:
        return self.hass.data[DATA_KEY]

    def _finish_without_change(self) -> ConfigFlowResult:
        # Same options as before, so the entry isn't reloaded
        return self.async_create_entry(data=dict(self.config_entry.options))

    def _finish_with(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["layout", "preset_add"]
        if self._data.stored:
            menu += ["preset_edit", "preset_delete"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_layout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        options = self.config_entry.options
        if user_input is not None:
            try:
                modules = parse_modules(user_input[CONF_MODULES])
            except ValueError as err:
                errors[CONF_MODULES] = "invalid_modules"
                placeholders["error"] = str(err)
            else:
                return self._finish_with(
                    **{
                        CONF_MODULES: ", ".join(modules),
                        CONF_DIRECTION: user_input[CONF_DIRECTION],
                    }
                )
        current = user_input or options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MODULES, default=current.get(CONF_MODULES, DEFAULT_MODULES)
                ): str,
                vol.Required(
                    CONF_DIRECTION,
                    default=current.get(CONF_DIRECTION, DEFAULT_DIRECTION),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=DIRECTIONS,
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_DIRECTION,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="layout",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_preset_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "invalid_name"
            else:
                try:
                    await self._data.save_preset(name, user_input[CONF_TEXT])
                except ArtError as err:
                    errors[CONF_TEXT] = "invalid_preset"
                    placeholders["error"] = str(err)
                else:
                    return self._finish_without_change()
        schema = self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_TEXT): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            user_input,
        )
        return self.async_show_form(
            step_id="preset_add",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    def _pick_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME): SelectSelector(
                    SelectSelectorConfig(
                        options=sorted(self._data.stored),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def async_step_preset_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._data.stored:
            return self.async_abort(reason="no_presets")
        if user_input is not None:
            self._edit_name = user_input[CONF_NAME]
            return await self.async_step_preset_edit_text()
        return self.async_show_form(
            step_id="preset_edit", data_schema=self._pick_schema()
        )

    async def async_step_preset_edit_text(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        name = self._edit_name
        if name is None or name not in self._data.stored:
            return self.async_abort(reason="no_presets")
        errors: dict[str, str] = {}
        placeholders = {"name": name, "error": ""}
        if user_input is not None:
            try:
                await self._data.save_preset(name, user_input[CONF_TEXT])
            except ArtError as err:
                errors[CONF_TEXT] = "invalid_preset"
                placeholders["error"] = str(err)
            else:
                return self._finish_without_change()
        text = user_input[CONF_TEXT] if user_input else self._data.stored[name]
        schema = self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Required(CONF_TEXT): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            {CONF_TEXT: text},
        )
        return self.async_show_form(
            step_id="preset_edit_text",
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_preset_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._data.stored:
            return self.async_abort(reason="no_presets")
        if user_input is not None:
            await self._data.delete_preset(user_input[CONF_NAME])
            return self._finish_without_change()
        return self.async_show_form(
            step_id="preset_delete", data_schema=self._pick_schema()
        )
