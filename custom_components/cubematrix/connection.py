"""One lamp: LAN control, frames in the UDP session, and the LAN fallback"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

from . import protocol
from .layout import encode_leds
from .protocol import LampError, LanClient, stop_task
from .udp import UdpSession, UdpTooLarge, UdpUnavailable

_LOGGER = logging.getLogger(__name__)

# Smooth up to 40 fps was seen, but the lamp answers only about 30 datagrams a
# second and keep-alives and commands share that
UDP_MAX_FPS = 25.0
# Normal mode allows about 60 commands per minute
NORMAL_MAX_FPS = 1.0
# Placeholder: how often a still picture is sent again, set by a hardware check
STATIC_REFRESH_S: float | None = None
# After UDP failed, it is tried again at most this often, and only when the caller
# asks for it (the background does, a show does not)
UDP_RETRY_S = 300.0
# The lamp forgets a session without traffic
KEEP_ALIVE_S = 10.0
# Direct mode is sent again after "illegal request" at most this often, the
# replies to the frames already on the way would ask for it again
REACTIVATE_S = 0.5

# Any of these in a props message means the lamp left direct mode
DIRECT_LOST_PROPS = ("rgb", "ct", "hue", "sat", "color_mode", "flowing")

DIRECT = [{"mode": "direct"}]

UDP_FALLBACK = (
    "UDP session with %s doesn't work (%s); using normal mode "
    "(about 1 frame per second). See README: Connection"
)
TOO_BIG = (
    "Frames for %s are too big for UDP (%s); using normal mode "
    "(about 1 frame per second). See README: Connection"
)

Mode = Literal["udp", "normal", "offline"]


class CubeConnection:
    def __init__(
        self,
        host: str,
        *,
        lan_port: int | None = None,
        udp_port: int | None = None,
        udp_timeout: float = 0.5,
    ) -> None:
        self.host = host
        self.on_change: Callable[[], None] | None = None
        # How often direct mode was sent again after "illegal request", for debugging
        self.reactivations = 0
        self._lan_port = lan_port
        self._udp_port = udp_port
        self._udp_timeout = udp_timeout
        self._lan: LanClient | None = None
        self._session: UdpSession | None = None
        self._power_on = False
        # Direct mode set over LAN, for normal mode
        self._direct = False
        # Direct mode set in the UDP session
        self._udp_direct = False
        # Loop time of the last failed UDP try, None when UDP may be tried now
        self._udp_failed_at: float | None = None
        # The layout makes frames bigger than one datagram
        self._too_big = False
        self._keep_alive_task: asyncio.Task | None = None
        self._reactivate_task: asyncio.Task | None = None
        self._last_reactivate: float | None = None
        self._mode: Mode = "offline"
        self._available = False
        self._lock = asyncio.Lock()
        self._normal_lock = asyncio.Lock()
        self._frame_seq = 0
        self._last_normal_send: float | None = None

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def max_fps(self) -> float:
        return UDP_MAX_FPS if self._mode == "udp" else NORMAL_MAX_FPS

    @property
    def available(self) -> bool:
        return self._available

    async def ensure_ready(
        self, refresh: bool = False, retry_udp: bool = False
    ) -> None:
        async with self._lock:
            try:
                await self._prepare(refresh, retry_udp)
            except LampError:
                self._after_failure()
                raise

    async def send_frame(self, raw: bytes) -> None:
        if self._needs_ready():
            await self.ensure_ready()
        data = encode_leds(raw)
        if self._mode == "udp":
            try:
                self._send_udp(data)
                return
            except UdpTooLarge as err:
                if not self._too_big:
                    self._too_big = True
                    _LOGGER.warning(TOO_BIG, self.host, err)
                async with self._lock:
                    await self._stop_udp()
                await self.ensure_ready()
        await self._send_normal(data)

    async def power_off(self) -> None:
        async with self._lock:
            try:
                lan = await self._connect_lan()
                await lan.call("set_power", ["off", "smooth", 300])
            except LampError:
                self._after_failure()
                raise
            self._power_on = False
            self._direct = False
            # Nothing to draw, so no session to keep
            await self._stop_udp()

    async def close(self) -> None:
        async with self._lock:
            # A frame waiting in normal mode is dropped
            self._frame_seq += 1
            await self._stop_udp()
            lan, self._lan = self._lan, None
            if lan is not None:
                await lan.close()
            self._power_on = False
            self._direct = False
            self._set_state("offline", False)

    async def _prepare(self, refresh: bool, retry_udp: bool) -> None:
        lan = await self._connect_lan()
        if not self._power_on:
            await lan.call("set_power", ["on", "smooth", 300])
            self._power_on = True
        if self._udp_may_start(retry_udp):
            # Warn only on the first failure, a retry that fails again is quiet
            retry = self._udp_failed_at is not None
            try:
                await self._start_udp(refresh)
            except UdpUnavailable as err:
                await self._stop_udp()
                log = _LOGGER.debug if retry else _LOGGER.warning
                log(UDP_FALLBACK, self.host, err)
                self._udp_failed_at = asyncio.get_running_loop().time()
            else:
                if retry:
                    _LOGGER.info("UDP session with %s works again", self.host)
                self._udp_failed_at = None
                # LAN direct mode may go stale while UDP runs, a fallback sets it again
                self._direct = False
                self._set_state("udp", True)
                return
        if refresh or not self._direct:
            await lan.call("activate_fx_mode", DIRECT)
            self._direct = True
        self._set_state("normal", True)

    async def _connect_lan(self) -> LanClient:
        if self._lan is not None and not self._lan.closed:
            return self._lan
        if self._lan is not None:
            await self._lan.close()
            self._lan = None
        lan = LanClient()
        lan.on_notify = self._on_notify
        port = self._lan_port if self._lan_port is not None else protocol.LAN_PORT
        await lan.connect(self.host, port)
        self._lan = lan
        # A new connection knows nothing about the lamp state, maybe it restarted
        self._power_on = False
        self._direct = False
        await self._stop_udp()
        self._udp_failed_at = None
        return lan

    def _udp_may_start(self, retry_udp: bool) -> bool:
        if self._too_big:
            return False
        if self._udp_failed_at is None:
            return True
        waited = asyncio.get_running_loop().time() - self._udp_failed_at
        return retry_udp and waited >= UDP_RETRY_S

    async def _start_udp(self, refresh: bool) -> None:
        session = self._session
        if session is None:
            session = UdpSession(self.host, self._udp_port, timeout=self._udp_timeout)
            session.on_illegal = self._on_illegal
            session.on_refused = self._on_refused
            self._session = session
        # An old session that is refused or silent gets one new try
        for attempt in range(2):
            fresh = not session.is_open
            try:
                if fresh:
                    await session.open()
                    self._udp_direct = False
                if refresh or not self._udp_direct:
                    await session.call("activate_fx_mode", DIRECT)
                    self._udp_direct = True
                break
            except UdpUnavailable:
                if fresh or attempt:
                    raise
        if self._keep_alive_task is None or self._keep_alive_task.done():
            self._keep_alive_task = asyncio.get_running_loop().create_task(
                self._keep_alive(session), name=f"cubematrix keep-alive {self.host}"
            )

    async def _stop_udp(self) -> None:
        # The session goes first, so no reply starts a new task while the old ones stop
        session, self._session = self._session, None
        if session is not None:
            session.close()
        self._udp_direct = False
        keep_alive, self._keep_alive_task = self._keep_alive_task, None
        reactivate, self._reactivate_task = self._reactivate_task, None
        await stop_task(keep_alive)
        await stop_task(reactivate)

    async def _keep_alive(self, session: UdpSession) -> None:
        while True:
            await asyncio.sleep(KEEP_ALIVE_S)
            if not session.is_open:
                # The next frame or check opens a new one
                continue
            try:
                await session.keep_alive()
            except LampError as err:
                _LOGGER.debug("Keep-alive for %s failed: %s", self.host, err)

    def _send_udp(self, data: str) -> None:
        if self._session is None:
            raise UdpUnavailable("No UDP session")
        self._session.send_frame_cmd([data])

    async def _send_normal(self, data: str) -> None:
        self._frame_seq += 1
        seq = self._frame_seq
        loop = asyncio.get_running_loop()
        async with self._normal_lock:
            if seq != self._frame_seq:
                return
            if self._last_normal_send is not None:
                delay = self._last_normal_send + 1 / NORMAL_MAX_FPS - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                    if seq != self._frame_seq:
                        # A newer frame came in while this one waited
                        return
            if self._needs_ready():
                await self.ensure_ready()
            if self._lan is None:
                raise LampError("Not connected to the lamp")
            self._lan.send("update_leds", [data])
            self._last_normal_send = loop.time()

    def _udp_ready(self) -> bool:
        session = self._session
        return (
            session is not None
            and session.is_open
            and self._udp_direct
            and self._power_on
        )

    def _needs_ready(self) -> bool:
        if self._lan is None or self._lan.closed or self._mode == "offline":
            return True
        if self._mode == "udp":
            return not self._udp_ready()
        return not (self._direct and self._power_on)

    def _on_notify(self, params: dict) -> None:
        power = params.get("power")
        if power == "off":
            self._power_on = False
            self._direct = False
            self._udp_direct = False
        elif power == "on":
            self._power_on = True
        if any(key in params for key in DIRECT_LOST_PROPS):
            self._direct = False
            self._udp_direct = False

    def _on_illegal(self) -> None:
        # Normal: the lamp drops direct mode now and then over UDP
        if self._session is None:
            return
        now = asyncio.get_running_loop().time()
        if (
            self._last_reactivate is not None
            and now - self._last_reactivate < REACTIVATE_S
        ):
            return
        if self._reactivate_task is not None and not self._reactivate_task.done():
            return
        self._last_reactivate = now
        self.reactivations += 1
        _LOGGER.debug(
            "%s left direct mode, setting it again (%s times)",
            self.host,
            self.reactivations,
        )
        self._reactivate_task = asyncio.get_running_loop().create_task(
            self._reactivate(), name=f"cubematrix direct mode {self.host}"
        )

    async def _reactivate(self) -> None:
        session = self._session
        if session is None or not session.is_open:
            return
        try:
            await session.call("activate_fx_mode", DIRECT)
        except LampError as err:
            # The next frame finds the session gone and opens a new one
            _LOGGER.debug("Direct mode for %s not set again: %s", self.host, err)
            self._udp_direct = False

    def _on_refused(self) -> None:
        # The session is gone, the next frame opens a new one
        self._udp_direct = False

    def _after_failure(self) -> None:
        # An error reply leaves the LAN open, the lamp is still there
        if self._lan is None or self._lan.closed:
            self._set_state("offline", False)

    def _set_state(self, mode: Mode, available: bool) -> None:
        if mode == self._mode and available == self._available:
            return
        self._mode = mode
        self._available = available
        if self.on_change is not None:
            self.on_change()
