import asyncio

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.cubematrix.const import DOMAIN

from .conftest import settle

HOST = "10.0.0.1"
LIGHT = "light.cube_matrix_10_0_0_1"
SELECT = "select.cube_matrix_10_0_0_1_background"
SENSOR = "sensor.cube_matrix_10_0_0_1_playing"

HEART = """
name: heart
size: 5x5
palette: {x: '#ff0000'}
frames:
  - |
    .x.x.
    xxxxx
    xxxxx
    .xxx.
    ..x..
"""

TINY = {"name": "tiny", "size": "1x1", "palette": {"x": "#00ff00"}, "frames": [["x"]]}


@pytest.fixture
async def entry(hass, add_cube):
    return await add_cube(HOST)


@pytest.fixture
async def two_entries(hass, add_cube):
    return [await add_cube("10.0.0.1"), await add_cube("10.0.0.2")]


async def show(hass, **data):
    await hass.services.async_call(DOMAIN, "show", data, blocking=True)


async def test_show_preset_wait(hass, entry):
    player = entry.runtime_data.player
    seen = []
    player.add_listener(lambda: seen.append(player.playing))
    await show(hass, entity_id=LIGHT, preset="check", duration=0.05, wait=True)
    assert "check" in seen
    # The call returns when the event part ends, the background is back
    assert hass.states.get(SENSOR).state == "fire"


async def test_show_without_wait_returns_at_once(hass, entry):
    await show(hass, entity_id=LIGHT, preset="check", duration=30)
    await settle(hass)
    assert hass.states.get(SENSOR).state == "check"


async def test_show_inline_definition(hass, entry):
    await show(hass, entity_id=LIGHT, definition=HEART, repeat="forever", then="hold")
    await settle(hass)
    assert hass.states.get(SENSOR).state == "heart"
    # A mapping works too, a small art can be centred
    await show(hass, entity_id=LIGHT, definition=TINY, fit="center", duration=30)
    await settle(hass)
    assert hass.states.get(SENSOR).state == "tiny"


async def test_show_two_cubes_in_one_call(hass, two_entries):
    lights = ["light.cube_matrix_10_0_0_1", "light.cube_matrix_10_0_0_2"]
    await show(hass, entity_id=lights, preset="check", duration=30)
    await settle(hass)
    assert hass.states.get("sensor.cube_matrix_10_0_0_1_playing").state == "check"
    assert hass.states.get("sensor.cube_matrix_10_0_0_2_playing").state == "check"


async def test_show_on_off_cube_is_muted(hass, entry):
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": LIGHT}, blocking=True
    )
    await show(hass, entity_id=LIGHT, preset="check", duration=30, wait=True)
    await settle(hass)
    assert hass.states.get(SENSOR).state == "off"


async def test_show_wake_plays_on_off_cube_and_stays_off(hass, entry):
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": LIGHT}, blocking=True
    )
    player = entry.runtime_data.player
    seen = []
    player.add_listener(lambda: seen.append(player.playing))
    await show(hass, entity_id=LIGHT, preset="check", duration=0.05, wake=True, wait=True)
    await settle(hass)
    assert "check" in seen
    assert hass.states.get(LIGHT).state == "off"
    assert hass.states.get(SENSOR).state == "off"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"preset": "check", "definition": HEART}, "preset or definition"),
        ({}, "preset or definition"),
        ({"preset": "no_such_one"}, "no_such_one"),
        ({"definition": TINY}, "1x1"),
        ({"definition": "size: [nope"}, "invalid"),
        ({"preset": "check", "then": "no_such_then"}, "no_such_then"),
    ],
)
async def test_show_errors(hass, entry, data, message):
    with pytest.raises(ServiceValidationError, match=message):
        await show(hass, entity_id=LIGHT, **data)
    await settle(hass)
    assert hass.states.get(SENSOR).state == "fire"


async def test_set_background_updates_select(hass, entry):
    await hass.services.async_call(
        DOMAIN, "set_background", {"entity_id": LIGHT, "preset": "rainbow"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(SELECT).state == "rainbow"
    assert hass.states.get(SENSOR).state == "rainbow"
    await hass.services.async_call(
        DOMAIN, "set_background", {"entity_id": LIGHT, "preset": "off"}, blocking=True
    )
    await settle(hass)
    assert hass.states.get(SELECT).state == "off"
    with pytest.raises(ServiceValidationError, match="no_such_one"):
        await hass.services.async_call(
            DOMAIN,
            "set_background",
            {"entity_id": LIGHT, "preset": "no_such_one"},
            blocking=True,
        )
    assert hass.states.get(SELECT).state == "off"


async def test_stop_and_reload_presets(hass, add_cube, tmp_path):
    folder = tmp_path / "cubematrix"
    folder.mkdir()
    hass.config.config_dir = str(tmp_path)
    await add_cube(HOST)
    await show(hass, entity_id=LIGHT, preset="check", repeat="forever", then="hold")
    await settle(hass)
    assert hass.states.get(SENSOR).state == "check"
    await hass.services.async_call(DOMAIN, "stop", {"entity_id": LIGHT}, blocking=True)
    await settle(hass)
    assert hass.states.get(SENSOR).state == "fire"

    (folder / "heart.yaml").write_text(HEART)
    await hass.services.async_call(DOMAIN, "reload_presets", {}, blocking=True)
    await settle(hass)
    assert "heart" in hass.states.get(SELECT).attributes["options"]
    await show(hass, entity_id=LIGHT, preset="heart", duration=30)
    await settle(hass)
    assert hass.states.get(SENSOR).state == "heart"


async def test_show_wait_covers_whole_event(hass, entry):
    task = asyncio.ensure_future(
        show(hass, entity_id=LIGHT, preset="check", duration=30, wait=True)
    )
    # Not settle(), it would wait for the running action too
    for _ in range(50):
        await asyncio.sleep(0)
    assert hass.states.get(SENSOR).state == "check"
    assert not task.done()
    await hass.services.async_call(DOMAIN, "stop", {"entity_id": LIGHT}, blocking=True)
    await asyncio.wait_for(task, 1)
