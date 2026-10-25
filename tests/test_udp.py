import asyncio
import socket

import pytest

from custom_components.cubematrix import udp
from custom_components.cubematrix.protocol import LampError
from custom_components.cubematrix.udp import (
    TokenRefused,
    UdpSession,
    UdpTooLarge,
    UdpUnavailable,
    parse_reply,
)

from .fake_lamp import FakeLamp, free_port


@pytest.fixture
async def lamp(loopback):
    fake = FakeLamp("127.0.0.2")
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def session(lamp):
    made = UdpSession(lamp.ip, lamp.udp_port, timeout=0.1)
    yield made
    made.close()


async def wait_until(check, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


def test_parse_reply_reads_the_lamps_broken_json():
    line = b'{"id":704,"error":{"code":6, "message":illegal request},"token":"2c33"}'
    assert parse_reply(line) == {
        "id": 704,
        "error": {"code": 6, "message": "illegal request"},
        "token": "2c33",
    }


def test_parse_reply_valid_and_garbage():
    assert parse_reply(b'{"id":2,"result":["ok"]}') == {"id": 2, "result": ["ok"]}
    assert parse_reply(b"not json") is None
    assert parse_reply(b"[1, 2]") is None


async def test_open_and_token_on_every_datagram(lamp, session):
    await session.open()
    assert session.is_open
    assert await session.call("activate_fx_mode", [{"mode": "direct"}]) == ["ok"]
    session.send_frame_cmd(["/wAA" * 25])
    await session.keep_alive()
    await wait_until(lambda: len(lamp.datagrams) == 4)
    assert [message["method"] for message in lamp.datagrams] == [
        "udp_sess_new",
        "activate_fx_mode",
        "update_leds",
        "udp_sess_keep_alive",
    ]
    assert len(set(lamp.tokens())) == 1
    assert lamp.tokens()[0] in lamp.sessions
    assert lamp.datagrams[3]["params"] == ["keeplive_interval", 10]


async def test_error_reply_raises(lamp, session):
    lamp.errors["set_power"] = {"code": 1, "message": "invalid params"}
    await session.open()
    with pytest.raises(LampError, match="invalid params") as info:
        await session.call("set_power", ["on"])
    assert not isinstance(info.value, UdpUnavailable)
    assert session.is_open


async def test_refused_token_closes_the_session(lamp, session):
    await session.open()
    lamp.forget_sessions()
    with pytest.raises(TokenRefused):
        await session.call("activate_fx_mode", [{"mode": "direct"}])
    assert not session.is_open
    await session.open()
    assert await session.call("activate_fx_mode", [{"mode": "direct"}]) == ["ok"]


async def test_no_answer(lamp, session):
    lamp.udp_silent = True
    with pytest.raises(UdpUnavailable):
        await session.open()
    lamp.udp_silent = False
    await session.open()
    lamp.udp_silent = True
    with pytest.raises(UdpUnavailable):
        await session.call("activate_fx_mode", [{"mode": "direct"}])
    # A session that doesn't answer is gone
    assert not session.is_open
    # Sent again before giving up, the lamp may lose a datagram
    assert [m["method"] for m in lamp.datagrams].count("activate_fx_mode") == (
        udp.ATTEMPTS
    )


async def test_nothing_listens(loopback):
    session = UdpSession("127.0.0.2", free_port(kind=socket.SOCK_DGRAM), timeout=0.1)
    try:
        with pytest.raises(UdpUnavailable):
            await session.open()
    finally:
        session.close()


async def test_frame_replies_call_back(lamp, session):
    illegal, refused = [], []
    session.on_illegal = lambda: illegal.append(True)
    session.on_refused = lambda: refused.append(True)
    await session.open()
    # Not in direct mode: the lamp answers each frame with code 6
    session.send_frame_cmd(["/wAA" * 25])
    session.send_frame_cmd(["/wAA" * 25])
    await wait_until(lambda: len(illegal) == 2)
    lamp.forget_sessions()
    session.send_frame_cmd(["/wAA" * 25])
    await wait_until(lambda: refused)
    assert not session.is_open
    with pytest.raises(UdpUnavailable):
        session.send_frame_cmd(["/wAA" * 25])


async def test_late_refusal_from_an_old_session_is_ignored(lamp, session):
    refused = []
    session.on_refused = lambda: refused.append(True)
    await session.open()
    lamp.forget_sessions()
    await session.open()
    # A reply to a frame sent in the first session comes in late
    session._received(b'{"id":1,"error":{"code":-1, "message":invalid parameter}}')
    assert session.is_open
    assert refused == []


async def test_too_large_frame(lamp, session):
    await session.open()
    with pytest.raises(UdpTooLarge):
        session.send_frame_cmd(["A" * udp.MAX_DATAGRAM])
    assert session.is_open


async def test_port_resolved_at_open(lamp, monkeypatch):
    monkeypatch.setattr(udp, "UDP_PORT", lamp.udp_port)
    session = UdpSession(lamp.ip)
    try:
        await session.open()
        assert session.is_open
    finally:
        session.close()
