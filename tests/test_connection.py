import asyncio
import logging
import socket

import pytest

from custom_components.cubematrix import connection, udp
from custom_components.cubematrix import player as player_module
from custom_components.cubematrix.connection import CubeConnection
from custom_components.cubematrix.effects import EFFECTS
from custom_components.cubematrix.layout import Layout
from custom_components.cubematrix.player import Player
from custom_components.cubematrix.presets import PresetLibrary
from custom_components.cubematrix.protocol import LampError, LanClient, command

from .fake_lamp import FakeLamp, free_port

RED_FRAME = bytes([255, 0, 0]) * 25
RED_LEDS = "/wAA" * 25
GREEN_FRAME = bytes([0, 255, 0]) * 25
GREEN_LEDS = "AP8A" * 25
DIRECT = "activate_fx_mode"


@pytest.fixture
async def lamp(loopback):
    fake = FakeLamp("127.0.0.2")
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def make_conn():
    made = []

    def make(fake, **kwargs):
        kwargs.setdefault("udp_timeout", 0.05)
        conn = CubeConnection(
            fake.ip, lan_port=fake.port, udp_port=fake.udp_port, **kwargs
        )
        made.append(conn)
        return conn

    yield make
    for conn in made:
        await conn.close()


async def wait_until(check, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_command_is_a_json_line():
    assert command(3, "set_power", ["on"]) == (
        b'{"id":3,"method":"set_power","params":["on"]}\r\n'
    )


async def test_ready_opens_session_and_sets_direct_in_it(lamp, make_conn):
    conn = make_conn(lamp)
    changes = []
    conn.on_change = lambda: changes.append((conn.available, conn.mode))
    await conn.ensure_ready()
    assert lamp.calls == ["set_power"]
    assert lamp.udp_calls == ["udp_sess_new", DIRECT]
    # Direct mode only in the session, not over LAN
    assert not lamp.direct
    assert list(lamp.sessions.values()) == [True]
    assert changes == [(True, "udp")]
    assert conn.mode == "udp"
    assert conn.max_fps == connection.UDP_MAX_FPS == 25.0
    assert conn.available
    assert lamp.power == "on"


async def test_frames_reach_lamp_over_udp_with_token(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.send_frame(RED_FRAME)
    await conn.send_frame(GREEN_FRAME)
    await wait_until(lambda: len(lamp.frames) == 2)
    assert lamp.frames == [RED_LEDS, GREEN_LEDS]
    assert lamp.frame_sources == ["udp", "udp"]
    tokens = lamp.tokens()
    assert len(tokens) == 3
    assert set(tokens) == set(lamp.sessions)


async def test_illegal_reply_reactivates_once(lamp, make_conn, caplog):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.drop_direct_quietly()
    with caplog.at_level(logging.DEBUG, logger="custom_components.cubematrix"):
        # All of them get "illegal request", only one re-activation follows
        for _ in range(5):
            await conn.send_frame(RED_FRAME)
        await wait_until(lambda: lamp.udp_calls.count(DIRECT) == 2)
        await asyncio.sleep(0.1)
    assert lamp.udp_calls.count(DIRECT) == 2
    assert lamp.udp_calls.count("udp_sess_new") == 1
    assert conn.reactivations == 1
    assert warnings_in(caplog) == []
    await conn.send_frame(GREEN_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.frames == [GREEN_LEDS]
    assert conn.mode == "udp"


async def test_reactivation_again_after_the_window(lamp, make_conn, monkeypatch):
    monkeypatch.setattr(connection, "REACTIVATE_S", 0.1)
    conn = make_conn(lamp)
    await conn.ensure_ready()
    for _ in range(2):
        lamp.drop_direct_quietly()
        await conn.send_frame(RED_FRAME)
        await wait_until(lambda: lamp.frames or all(lamp.sessions.values()))
        await asyncio.sleep(0.15)
    assert conn.reactivations == 2
    assert lamp.udp_calls.count(DIRECT) == 3


async def test_refused_token_on_frame_opens_new_session(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.forget_sessions()
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: not conn._udp_ready())
    await conn.send_frame(GREEN_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.frames == [GREEN_LEDS]
    assert lamp.udp_calls == ["udp_sess_new", DIRECT, "udp_sess_new", DIRECT]
    assert conn.mode == "udp"


async def test_refused_token_on_refresh_opens_new_session(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.forget_sessions()
    await conn.ensure_ready(refresh=True)
    # The refused call isn't counted by the fake lamp
    assert lamp.udp_calls == ["udp_sess_new", DIRECT, "udp_sess_new", DIRECT]
    assert conn.mode == "udp"
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)


async def test_recovers_after_direct_lost(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.drop_direct()
    # The props message has to arrive before the next frame
    await wait_until(lambda: not conn._udp_ready())
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.udp_calls.count(DIRECT) == 2
    assert lamp.frames == [RED_LEDS]


async def test_power_off_notification_sets_power_again(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.notify({"power": "off"})
    lamp.power = "off"
    await wait_until(lambda: not conn._udp_ready())
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.calls.count("set_power") == 2
    assert lamp.udp_calls.count(DIRECT) == 2


async def test_keep_alive_while_streaming(lamp, make_conn, monkeypatch):
    monkeypatch.setattr(connection, "KEEP_ALIVE_S", 0.05)
    conn = make_conn(lamp)
    await conn.ensure_ready()

    def keep_alives():
        return [m for m in lamp.datagrams if m["method"] == "udp_sess_keep_alive"]

    await wait_until(lambda: len(keep_alives()) >= 3)
    assert all(m["params"] == ["keeplive_interval", 10] for m in keep_alives())
    # The light is off: no session to keep
    await conn.power_off()
    count = len(lamp.datagrams)
    await asyncio.sleep(0.15)
    assert len(lamp.datagrams) == count


async def test_default_keep_alive_interval():
    assert connection.KEEP_ALIVE_S == 10.0
    assert connection.REACTIVATE_S == 0.5


async def test_refused_keep_alive_opens_new_session(lamp, make_conn, monkeypatch):
    monkeypatch.setattr(connection, "KEEP_ALIVE_S", 0.05)
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.forget_sessions()
    await wait_until(lambda: not conn._udp_ready())
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.udp_calls.count("udp_sess_new") == 2


async def test_two_lamps_each_get_their_frames(loopback):
    first = FakeLamp("127.0.0.2")
    second = FakeLamp("127.0.0.3")
    await first.start()
    await second.start()
    conn_first = CubeConnection(first.ip, lan_port=first.port, udp_port=first.udp_port)
    conn_second = CubeConnection(
        second.ip, lan_port=second.port, udp_port=second.udp_port
    )
    try:
        await conn_first.send_frame(RED_FRAME)
        await conn_second.send_frame(GREEN_FRAME)
        await wait_until(lambda: first.frames and second.frames)
        await asyncio.sleep(0.05)
        assert conn_first.mode == conn_second.mode == "udp"
        assert first.frames == [RED_LEDS]
        assert second.frames == [GREEN_LEDS]
        assert first.frame_sources == second.frame_sources == ["udp"]
    finally:
        await conn_first.close()
        await conn_second.close()
        await first.stop()
        await second.stop()


async def test_fallback_normal_when_udp_silent(lamp, make_conn, caplog):
    lamp.udp_silent = True
    conn = make_conn(lamp)
    with caplog.at_level(logging.WARNING):
        await conn.ensure_ready()
    assert conn.mode == "normal"
    assert conn.max_fps == connection.NORMAL_MAX_FPS == 1.0
    warnings = warnings_in(caplog)
    assert len(warnings) == 1
    assert "README: Connection" in warnings[0]
    assert "127.0.0.2" in warnings[0]
    assert lamp.calls == ["set_power", DIRECT]
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.frames == [RED_LEDS]
    assert lamp.frame_sources == ["lan"]


async def test_udp_retried_only_when_asked_after_interval(
    lamp, make_conn, monkeypatch, caplog
):
    monkeypatch.setattr(connection, "UDP_RETRY_S", 0.3)
    lamp.udp_silent = True
    conn = make_conn(lamp)
    await conn.ensure_ready()
    assert conn.mode == "normal"
    # Still silent at the first retry: no second warning
    await asyncio.sleep(0.35)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        await conn.ensure_ready(retry_udp=True)
    assert conn.mode == "normal"
    assert warnings_in(caplog) == []
    lamp.udp_silent = False
    # Too soon after the last try
    await conn.ensure_ready(retry_udp=True)
    assert conn.mode == "normal"
    await asyncio.sleep(0.35)
    # Shows and frames never wait for UDP
    await conn.ensure_ready()
    await conn.ensure_ready(refresh=True)
    await conn.send_frame(RED_FRAME)
    assert conn.mode == "normal"
    news = [m for m in lamp.datagrams if m["method"] == "udp_sess_new"]
    assert len(news) == 2 * udp.ATTEMPTS
    await conn.ensure_ready(retry_udp=True)
    assert conn.mode == "udp"
    assert conn.max_fps == connection.UDP_MAX_FPS
    await conn.send_frame(GREEN_FRAME)
    await wait_until(lambda: "udp" in lamp.frame_sources)


async def test_fallback_sets_lan_direct_again(lamp, make_conn, monkeypatch):
    monkeypatch.setattr(connection, "UDP_RETRY_S", 0)
    monkeypatch.setattr(connection, "KEEP_ALIVE_S", 0.05)
    lamp.udp_silent = True
    conn = make_conn(lamp)
    await conn.ensure_ready()
    assert conn.mode == "normal"
    lamp.udp_silent = False
    await conn.ensure_ready(retry_udp=True)
    assert conn.mode == "udp"
    # While UDP runs the lamp loses LAN direct mode without a message
    lamp.direct = False
    lamp.udp_silent = True
    # The keep-alive gets no answer, so the session is gone
    await wait_until(lambda: not conn._udp_ready())
    await conn.send_frame(RED_FRAME)
    assert conn.mode == "normal"
    await wait_until(lambda: lamp.frames)
    assert lamp.frame_sources == ["lan"]
    assert lamp.calls.count(DIRECT) == 2


async def test_session_lost_later_warns_and_falls_back(lamp, make_conn, caplog):
    conn = make_conn(lamp)
    await conn.ensure_ready()
    lamp.udp_silent = True
    lamp.forget_sessions()
    with caplog.at_level(logging.WARNING):
        # The refresh gets no answer, the new session neither
        await conn.ensure_ready(refresh=True)
    assert conn.mode == "normal"
    assert len(warnings_in(caplog)) == 1
    await conn.send_frame(RED_FRAME)
    await wait_until(lambda: lamp.frames)
    assert lamp.frame_sources == ["lan"]


async def test_frame_too_big_for_udp_uses_lan(lamp, make_conn, caplog, monkeypatch):
    monkeypatch.setattr(connection, "NORMAL_MAX_FPS", 100.0)
    conn = make_conn(lamp)
    big = bytes([255, 0, 0]) * 25 * 8
    with caplog.at_level(logging.WARNING):
        await conn.send_frame(big)
        await conn.send_frame(big)
    await wait_until(lambda: len(lamp.frames) == 2)
    assert lamp.frame_sources == ["lan", "lan"]
    assert conn.mode == "normal"
    assert conn.max_fps == connection.NORMAL_MAX_FPS
    warnings = warnings_in(caplog)
    assert len(warnings) == 1
    assert "too big" in warnings[0]
    # No session left behind
    assert not conn._udp_ready()


async def test_six_matrices_fit_in_a_datagram(lamp, make_conn):
    conn = make_conn(lamp)
    await conn.send_frame(bytes([255, 0, 0]) * 25 * 6)
    await wait_until(lambda: lamp.frames)
    assert lamp.frame_sources == ["udp"]


async def test_normal_mode_keeps_only_newest_waiting_frame(
    lamp, make_conn, monkeypatch
):
    monkeypatch.setattr(connection, "NORMAL_MAX_FPS", 5.0)
    lamp.udp_silent = True
    conn = make_conn(lamp)
    await conn.send_frame(RED_FRAME)
    # Both wait for the rate limit, the newer one replaces the older one
    await asyncio.gather(conn.send_frame(bytes(75)), conn.send_frame(GREEN_FRAME))
    await wait_until(lambda: len(lamp.frames) >= 2)
    await asyncio.sleep(0.05)
    assert lamp.frames == [RED_LEDS, GREEN_LEDS]


async def test_error_reply_raises(lamp, make_conn):
    lamp.errors[DIRECT] = {"code": 1, "message": "unsupported"}
    conn = make_conn(lamp)
    with pytest.raises(LampError, match="unsupported"):
        await conn.ensure_ready()
    # An error reply means the lamp is still there
    assert not conn._lan.closed


async def test_call_times_out(lamp):
    lamp.silent.add("get_prop")
    client = LanClient()
    await client.connect(lamp.ip, lamp.port)
    try:
        with pytest.raises(LampError):
            await client.call("get_prop", ["power"], timeout=0.1)
        assert client.closed
    finally:
        await client.close()


async def test_notify_callback(lamp):
    client = LanClient()
    seen = []
    client.on_notify = seen.append
    await client.connect(lamp.ip, lamp.port)
    try:
        assert await client.call("get_prop", ["model"]) == ["CubeMatrix"]
        lamp.drop_direct()
        await wait_until(lambda: seen)
        assert seen == [{"color_mode": 1}]
    finally:
        await client.close()


async def test_ports_resolved_at_call_time(lamp, monkeypatch):
    from custom_components.cubematrix import protocol

    monkeypatch.setattr(protocol, "LAN_PORT", lamp.port)
    monkeypatch.setattr(udp, "UDP_PORT", lamp.udp_port)
    conn = CubeConnection(lamp.ip)
    try:
        await conn.ensure_ready()
        assert conn.available
        assert conn.mode == "udp"
    finally:
        await conn.close()


async def test_unreachable_sets_unavailable(loopback):
    conn = CubeConnection("127.0.0.2", lan_port=free_port())
    with pytest.raises(LampError):
        await conn.ensure_ready()
    assert conn.available is False
    assert conn.mode == "offline"
    # The next call tries again
    with pytest.raises(LampError):
        await conn.send_frame(RED_FRAME)
    await conn.close()


async def test_lamp_comes_back_after_failure(loopback, make_conn):
    fake = FakeLamp("127.0.0.2")
    fake.port = free_port()
    fake.udp_port = free_port(kind=socket.SOCK_DGRAM)
    conn = make_conn(fake)
    with pytest.raises(LampError):
        await conn.ensure_ready()
    await fake.start(fake.port)
    try:
        await conn.ensure_ready()
        assert conn.available
        assert conn.mode == "udp"
    finally:
        await conn.close()
        await fake.stop()


async def test_close_ends_the_session(lamp, make_conn, monkeypatch):
    monkeypatch.setattr(connection, "KEEP_ALIVE_S", 0.05)
    conn = make_conn(lamp)
    await conn.ensure_ready()
    await conn.close()
    assert conn.mode == "offline"
    count = len(lamp.datagrams)
    await asyncio.sleep(0.15)
    assert len(lamp.datagrams) == count


def player_for(conn):
    library = PresetLibrary(dict(EFFECTS), {}, {})
    return Player(conn, Layout(), lambda: library)


async def test_player_background_runs_fast_on_real_connection(lamp, make_conn):
    conn = make_conn(lamp)
    player = player_for(conn)
    try:
        await player.turn_on()
        await asyncio.sleep(1.0)
        # Fire at 10 fps over UDP, not 1 fps from the offline start
        assert len(lamp.frames) >= 7
        assert set(lamp.frame_sources) == {"udp"}
    finally:
        await player.shutdown()


async def test_player_brings_udp_back_after_failure(lamp, make_conn, monkeypatch):
    monkeypatch.setattr(connection, "UDP_RETRY_S", 0.3)
    monkeypatch.setattr(player_module, "WATCHDOG_S", 0.2)
    lamp.udp_silent = True
    conn = make_conn(lamp)
    player = player_for(conn)
    try:
        await player.turn_on()
        await wait_until(lambda: conn.mode == "normal")
        lamp.udp_silent = False
        await wait_until(lambda: conn.mode == "udp")
        await wait_until(lambda: "udp" in lamp.frame_sources)
    finally:
        await player.shutdown()
