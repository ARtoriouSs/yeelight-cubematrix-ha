import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF
from homeassistant.core import State
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.cubematrix import protocol
from custom_components.cubematrix.const import DOMAIN, STORE_KEY

from .fake_lamp import free_port

GOOD = """
name: good_one
size: 1x1
palette: {x: '#ff0000'}
frames: [[x]]
"""

BAD = """
name: bad_one
size: 2x2
palette: {x: '#ff0000'}
frames: [[x]]
"""


async def test_setup_entry_builds_runtime(hass, fake_lamp):
    # The light was off before the restart, so nothing turns the lamp on
    mock_restore_cache(hass, [State("light.cube_matrix_127_0_0_2", STATE_OFF)])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cube Matrix (127.0.0.2)",
        data={"host": fake_lamp.ip},
        unique_id=fake_lamp.ip,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("light.cube_matrix_127_0_0_2").state == STATE_OFF
    runtime = entry.runtime_data
    # Setup only checks the lamp answers, it doesn't turn it on
    assert fake_lamp.calls == ["get_prop"]
    assert fake_lamp.power == "off"
    assert not fake_lamp.direct
    assert runtime.connection.mode == "offline"
    assert runtime.connection.host == fake_lamp.ip
    assert runtime.player._layout.modules == ("matrix",)
    assert runtime.player._layout.direction == "horizontal"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_not_ready_when_unreachable(hass, loopback, monkeypatch):
    monkeypatch.setattr(protocol, "LAN_PORT", free_port())
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "127.0.0.2"},
        unique_id="127.0.0.2",
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None
    # Unload cancels the retry timer
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_unload_closes_udp_session(hass, entry, fake_lamp):
    connection = entry.runtime_data.connection
    # The light does this in use
    await connection.ensure_ready()
    assert connection.mode == "udp"
    assert fake_lamp.udp_calls == ["udp_sess_new", "activate_fx_mode"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert connection.mode == "offline"
    assert connection._session is None
    assert connection._keep_alive_task is None


async def test_old_music_port_option_is_ignored(hass, fake_lamp):
    # Saved by an older version, before frames went over UDP
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": fake_lamp.ip},
        options={"music_port": 55440, "modules": "matrix, matrix"},
        unique_id=fake_lamp.ip,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.player._layout.modules == ("matrix", "matrix")
    await entry.runtime_data.connection.ensure_ready()
    assert entry.runtime_data.connection.mode == "udp"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_not_ready_on_error_reply(hass, fake_lamp):
    fake_lamp.errors["get_prop"] = {"code": -1, "message": "busy"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": fake_lamp.ip},
        unique_id=fake_lamp.ip,
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fake_lamp.calls == ["get_prop"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_presets_shared_by_entries(hass, entry):
    data = hass.data[DOMAIN]
    await data.save_preset("good_one", GOOD)
    assert entry.runtime_data.player._library().source("good_one") == "stored"


async def test_stored_presets_loaded_on_setup(hass, hass_storage):
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {"presets": {"good_one": GOOD, "bad_one": BAD}},
    }
    assert await async_setup_component(hass, DOMAIN, {})
    data = hass.data[DOMAIN]
    # A stored preset that no longer parses stays stored but can't be played
    assert set(data.stored) == {"good_one", "bad_one"}
    library = data.library()
    assert library.source("good_one") == "stored"
    assert library.get("bad_one") is None


async def test_file_presets_loaded_with_errors(hass, tmp_path, caplog):
    folder = tmp_path / "cubematrix"
    folder.mkdir()
    (folder / "good.yaml").write_text(GOOD)
    (folder / "bad.yaml").write_text(BAD)
    hass.config.config_dir = str(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert await async_setup_component(hass, DOMAIN, {})
    data = hass.data[DOMAIN]
    library = data.library()
    assert library.source("good_one") == "file"
    assert library.get("bad_one") is None
    assert len(data.file_errors) == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("bad.yaml" in message for message in warnings)


async def test_reload_files_notifies(hass, tmp_path):
    folder = tmp_path / "cubematrix"
    folder.mkdir()
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, DOMAIN, {})
    data = hass.data[DOMAIN]
    changes = []
    unsub = data.add_listener(lambda: changes.append(True))
    (folder / "good.yaml").write_text(GOOD)
    await data.reload_files()
    assert data.library().source("good_one") == "file"
    assert changes == [True]
    unsub()
    await data.reload_files()
    assert changes == [True]
