from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cubematrix import protocol
from custom_components.cubematrix.const import (
    CONF_DIRECTION,
    CONF_MODULES,
    DOMAIN,
    STORE_KEY,
)

from .fake_lamp import free_port

HOST = "127.0.0.2"

ARROW = """
name: arrow_up
size: 1x1
palette: {x: '#ff0000'}
frames: [[x]]
"""

SHORT_ROW = """
name: broken
size: 2x2
palette: {x: '#ff0000'}
frames:
  - |
    xx
    x
"""

TWO_ARTS = """
- {name: one, size: 1x1, palette: {x: '#ff0000'}, frames: [[x]]}
- {name: two, size: 1x1, palette: {x: '#00ff00'}, frames: [[x]]}
"""


@pytest.fixture
def no_setup():
    """The flow tests don't need the entry to start"""
    with patch(
        "custom_components.cubematrix.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_entry(hass, fake_lamp, no_setup):
    result = await start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cube Matrix (127.0.0.2)"
    assert result["data"] == {"host": HOST}
    assert result["result"].unique_id == HOST
    assert "get_prop" in fake_lamp.calls


async def test_user_flow_cannot_connect(hass, loopback, monkeypatch, no_setup):
    monkeypatch.setattr(protocol, "LAN_PORT", free_port())
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_wrong_model(hass, fake_lamp, no_setup):
    fake_lamp.model = "cubelite"
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_model"}


async def test_user_flow_duplicate_aborts(hass, fake_lamp, no_setup):
    MockConfigEntry(domain=DOMAIN, data={"host": HOST}, unique_id=HOST).add_to_hass(
        hass
    )
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def open_options(hass, entry, step):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_options_layout_reloads(hass, entry):
    old_runtime = entry.runtime_data
    result = await open_options(hass, entry, "layout")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MODULES: "matrix, matrix", CONF_DIRECTION: "vertical"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_MODULES] == "matrix, matrix"
    assert entry.options[CONF_DIRECTION] == "vertical"
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert entry.runtime_data is not old_runtime
    assert entry.runtime_data.player._layout.modules == ("matrix", "matrix")
    assert entry.runtime_data.player._layout.direction == "vertical"


async def test_options_layout_invalid(hass, entry):
    old_options = dict(entry.options)
    result = await open_options(hass, entry, "layout")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MODULES: "lamp", CONF_DIRECTION: "horizontal"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_MODULES: "invalid_modules"}
    assert dict(entry.options) == old_options


async def test_options_preset_add_and_priority(hass, entry, hass_storage):
    data = hass.data[DOMAIN]
    assert data.library().source("arrow_up") == "builtin"
    changes = []
    data.add_listener(lambda: changes.append(True))
    old_runtime = entry.runtime_data
    result = await open_options(hass, entry, "preset_add")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "arrow_up", "text": ARROW}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert changes == [True]
    library = data.library()
    assert library.source("arrow_up") == "stored"
    assert (library.get("arrow_up").width, library.get("arrow_up").height) == (1, 1)
    # A preset change doesn't reload the entry
    assert entry.runtime_data is old_runtime
    assert hass_storage[STORE_KEY]["data"]["presets"] == {"arrow_up": ARROW}

    # A fresh load from the store sees the same preset
    from custom_components.cubematrix.data import CubeMatrixData

    fresh = await CubeMatrixData.async_load(hass)
    assert fresh.stored == {"arrow_up": ARROW}
    assert fresh.library().source("arrow_up") == "stored"


async def test_options_preset_add_invalid(hass, entry, hass_storage):
    result = await open_options(hass, entry, "preset_add")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "broken", "text": SHORT_ROW}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"text": "invalid_preset"}
    assert "row" in result["description_placeholders"]["error"]
    assert hass.data[DOMAIN].stored == {}
    assert STORE_KEY not in hass_storage


async def test_options_preset_add_needs_one_art(hass, entry):
    result = await open_options(hass, entry, "preset_add")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "two", "text": TWO_ARTS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"text": "invalid_preset"}
    assert hass.data[DOMAIN].stored == {}


async def test_options_preset_edit(hass, entry):
    data = hass.data[DOMAIN]
    await data.save_preset("dot", ARROW)
    result = await open_options(hass, entry, "preset_edit")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "dot"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preset_edit_text"
    # The current text is filled in
    schema_keys = {str(key): key for key in result["data_schema"].schema}
    assert schema_keys["text"].description["suggested_value"] == ARROW
    new_text = ARROW.replace("#ff0000", "#00ff00")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"text": new_text}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert data.stored == {"dot": new_text}
    assert data.library().get("dot").name == "dot"


async def test_options_preset_delete(hass, entry, hass_storage):
    data = hass.data[DOMAIN]
    await data.save_preset("arrow_up", ARROW)
    assert data.library().source("arrow_up") == "stored"
    result = await open_options(hass, entry, "preset_delete")
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "arrow_up"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert data.stored == {}
    assert data.library().source("arrow_up") == "builtin"
    assert hass_storage[STORE_KEY]["data"]["presets"] == {}


async def test_options_menu_hides_edit_without_presets(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert list(result["menu_options"]) == ["layout", "preset_add"]
    await hass.data[DOMAIN].save_preset("dot", ARROW)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert list(result["menu_options"]) == [
        "layout",
        "preset_add",
        "preset_edit",
        "preset_delete",
    ]
