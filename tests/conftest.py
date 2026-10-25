import asyncio

import pytest
import pytest_socket
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cubematrix import protocol, udp
from custom_components.cubematrix.const import DOMAIN

from .fake_lamp import FakeLamp


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    # Only tests that use hass need it, the pure core tests stay without HA
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture
def loopback(socket_enabled):
    """Allow sockets to the whole 127.0.0.0/8 range, the fake lamps live there"""
    pytest_socket.socket_allow_hosts(["127.0.0.0/8"], allow_unix_socket=True)


@pytest.fixture
async def fake_lamp(loopback, monkeypatch):
    """A fake lamp on 127.0.0.2, the integration talks to its LAN and UDP ports"""
    fake = FakeLamp("127.0.0.2")
    await fake.start()
    monkeypatch.setattr(protocol, "LAN_PORT", fake.port)
    monkeypatch.setattr(udp, "UDP_PORT", fake.udp_port)
    yield fake
    await fake.stop()


@pytest.fixture
async def entry(hass, fake_lamp):
    """A loaded entry for the fake lamp"""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cube Matrix (127.0.0.2)",
        data={"host": fake_lamp.ip},
        unique_id=fake_lamp.ip,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


class FakeConnection:
    """Stands in for CubeConnection when the lamp itself isn't the point"""

    def __init__(self, host: str, **kwargs) -> None:
        self.host = host
        self.on_change = None
        self.max_fps = 30.0
        self.available = False
        self.mode = "offline"
        # Set to make every call fail, like a lamp that went away
        self.gone = False
        self.ready: list[bool] = []
        self.frames: list[bytes] = []
        self.power_offs = 0
        self.closed = False

    async def ensure_ready(self, refresh: bool = False, retry_udp: bool = False) -> None:
        if self.gone:
            self._set(False)
            raise protocol.LampError("lamp is gone")
        self.ready.append(refresh)
        self._set(True)

    async def send_frame(self, raw: bytes) -> None:
        if not self.available:
            await self.ensure_ready()
        self.frames.append(raw)

    async def power_off(self) -> None:
        if self.gone:
            raise protocol.LampError("lamp is gone")
        self.power_offs += 1

    async def close(self) -> None:
        self.closed = True
        self._set(False)

    def lose(self) -> None:
        self.gone = True
        self._set(False)

    def _set(self, available: bool) -> None:
        if available == self.available:
            return
        self.available = available
        self.mode = "udp" if available else "offline"
        if self.on_change is not None:
            self.on_change()


@pytest.fixture
def fake_cubes(monkeypatch):
    """Fake connections for every entry, by host. The setup probe always answers"""
    import custom_components.cubematrix as integration

    made: dict[str, FakeConnection] = {}

    def make(host: str, **kwargs) -> FakeConnection:
        made[host] = FakeConnection(host, **kwargs)
        return made[host]

    async def get_props(host: str, names: list[str]) -> list[str]:
        return ["on" for _ in names]

    monkeypatch.setattr(integration, "CubeConnection", make)
    monkeypatch.setattr(integration, "get_props", get_props)
    return made


@pytest.fixture
async def add_cube(hass, fake_cubes):
    """Set up an entry for a fake cube, unloaded at the end of the test"""
    entries = []

    async def add(host: str) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"Cube Matrix ({host})",
            data={"host": host},
            unique_id=host,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await settle(hass)
        entries.append(entry)
        return entry

    yield add
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def settle(hass) -> None:
    """Let the player tasks run, they are not tracked by hass"""
    for _ in range(5):
        await hass.async_block_till_done()
        for _ in range(10):
            await asyncio.sleep(0)
