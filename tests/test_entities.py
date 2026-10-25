import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.select import ATTR_OPTIONS
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.cubematrix.const import DOMAIN

from .conftest import settle

HOST = "10.0.0.1"
LIGHT = "light.cube_matrix_10_0_0_1"
SELECT = "select.cube_matrix_10_0_0_1_background"
SENSOR = "sensor.cube_matrix_10_0_0_1_playing"

SMILE = """
name: smile
size: 5x5
palette: {x: '#ffff00'}
frames:
  - |
    .....
    .x.x.
    .....
    x...x
    .xxx.
"""


@pytest.fixture
async def entry(hass, add_cube):
    return await add_cube(HOST)


async def call(hass, domain, service, data):
    await hass.services.async_call(domain, service, data, blocking=True)
    await settle(hass)


async def test_entities_created(hass, entry, device_registry):
    light = hass.states.get(LIGHT)
    assert light.state == STATE_ON
    assert light.attributes[ATTR_BRIGHTNESS] == 255
    assert light.attributes["supported_color_modes"] == ["brightness"]
    select = hass.states.get(SELECT)
    assert select.state == "fire"
    assert select.attributes[ATTR_OPTIONS][0] == "off"
    assert {"fire", "rainbow", "check"} <= set(select.attributes[ATTR_OPTIONS])
    assert hass.states.get(SENSOR).state == "fire"
    device = device_registry.async_get_device_by_identifier((DOMAIN, HOST), entry.entry_id)
    assert device.manufacturer == "Yeelight"
    assert device.model == "Cube Matrix"


async def test_light_off_on(hass, entry, fake_cubes):
    cube = fake_cubes[HOST]
    await call(hass, "light", "turn_off", {"entity_id": LIGHT})
    assert hass.states.get(LIGHT).state == STATE_OFF
    assert hass.states.get(SENSOR).state == "off"
    assert cube.power_offs == 1
    await call(hass, "light", "turn_on", {"entity_id": LIGHT, ATTR_BRIGHTNESS: 40})
    assert hass.states.get(LIGHT).state == STATE_ON
    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == 40
    assert entry.runtime_data.player.brightness == 40
    assert hass.states.get(SENSOR).state == "fire"


async def test_select_changes_background(hass, entry):
    await call(hass, "select", "select_option", {"entity_id": SELECT, "option": "rainbow"})
    assert entry.runtime_data.player.background == "rainbow"
    assert hass.states.get(SENSOR).state == "rainbow"
    await call(hass, "select", "select_option", {"entity_id": SELECT, "option": "off"})
    assert entry.runtime_data.player.background is None
    assert hass.states.get(SELECT).state == "off"
    assert hass.states.get(SENSOR).state == "off"


async def test_select_options_follow_presets(hass, entry):
    data = hass.data[DOMAIN]
    await data.save_preset("smile", SMILE)
    await settle(hass)
    assert "smile" in hass.states.get(SELECT).attributes[ATTR_OPTIONS]
    await call(hass, "select", "select_option", {"entity_id": SELECT, "option": "smile"})
    assert hass.states.get(SENSOR).state == "smile"
    # The background preset is deleted, so the cube goes back to fire
    await data.delete_preset("smile")
    await settle(hass)
    assert "smile" not in hass.states.get(SELECT).attributes[ATTR_OPTIONS]
    assert hass.states.get(SELECT).state == "fire"
    assert entry.runtime_data.player.background == "fire"
    assert hass.states.get(SENSOR).state == "fire"


async def test_state_restored_after_restart(hass, add_cube, fake_cubes):
    mock_restore_cache(
        hass,
        [
            State(LIGHT, STATE_ON, {ATTR_BRIGHTNESS: 80}),
            State(SELECT, "rainbow"),
        ],
    )
    entry = await add_cube(HOST)
    player = entry.runtime_data.player
    assert hass.states.get(LIGHT).state == STATE_ON
    assert player.brightness == 80
    assert player.background == "rainbow"
    assert hass.states.get(SELECT).state == "rainbow"
    assert hass.states.get(SENSOR).state == "rainbow"
    # Turning on readies the lamp
    assert fake_cubes[HOST].ready


async def test_state_restored_off(hass, add_cube, fake_cubes):
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State(LIGHT, STATE_OFF), {"brightness": 50}),
            (State(SELECT, "gone_preset"), {}),
        ],
    )
    entry = await add_cube(HOST)
    player = entry.runtime_data.player
    assert hass.states.get(LIGHT).state == STATE_OFF
    assert player.brightness == 50
    # A restored name that no longer exists falls back to fire
    assert hass.states.get(SELECT).state == "fire"
    assert hass.states.get(SENSOR).state == "off"
    # An off light sends nothing to the lamp
    cube = fake_cubes[HOST]
    assert cube.ready == []
    assert cube.frames == []
    assert cube.power_offs == 0
    # It can still be turned on
    assert hass.states.get(LIGHT).state != STATE_UNAVAILABLE
    await call(hass, "light", "turn_on", {"entity_id": LIGHT})
    assert hass.states.get(LIGHT).attributes[ATTR_BRIGHTNESS] == 50
    assert cube.ready


async def test_unavailable_when_lamp_lost(hass, entry, fake_cubes):
    cube = fake_cubes[HOST]
    assert hass.states.get(LIGHT).state == STATE_ON
    cube.lose()
    await settle(hass)
    assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE
    cube.gone = False
    await cube.ensure_ready()
    await settle(hass)
    assert hass.states.get(LIGHT).state == STATE_ON
