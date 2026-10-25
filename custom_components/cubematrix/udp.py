"""The lamp's UDP command session on port 55444, used for drawing

udp_sess_new answers with a token, every later datagram carries it. Commands get a
reply by id, update_leds gets none while it works. A refused token answers code -1,
a frame outside direct mode answers code 6. Direct mode set in a session holds only
for that session
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from .protocol import LampError

_LOGGER = logging.getLogger(__name__)

UDP_PORT = 55444
# The lamp drops bigger datagrams
MAX_DATAGRAM = 760
# A command is sent this many times before giving up, the lamp may lose a datagram.
# Commands set a state, so sending one twice does no harm
ATTEMPTS = 3
# The keep-alive setting, spelled like the lamp spells it
KEEP_ALIVE_PARAMS = ["keeplive_interval", 10]
CODE_TOKEN_REFUSED = -1
CODE_ILLEGAL = 6

# The lamp sends error messages without quotes: "message":illegal request}
_UNQUOTED_MESSAGE = re.compile(rb'("message"\s*:\s*)([^"\s][^}]*?)(\s*})')


class UdpUnavailable(LampError):
    """The session can't be opened, or the lamp stopped answering in it"""


class TokenRefused(UdpUnavailable):
    """The lamp doesn't know the session any more, a new one is needed"""


class UdpTooLarge(UdpUnavailable):
    """The datagram is bigger than the lamp takes"""


def parse_reply(line: bytes) -> dict[str, Any] | None:
    """Read one reply line, also the lamp's broken JSON. None for anything else"""
    try:
        message = json.loads(line)
    except ValueError:
        fixed = _UNQUOTED_MESSAGE.sub(
            lambda match: (
                match[1]
                + json.dumps(match[2].decode(errors="replace")).encode()
                + match[3]
            ),
            line,
        )
        try:
            message = json.loads(fixed)
        except ValueError:
            return None
    return message if isinstance(message, dict) else None


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, session: UdpSession) -> None:
        self._session = session

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self._session._received(data)

    def error_received(self, exc: Exception) -> None:
        # For example "port unreachable": nothing listens there
        self._session._failed(exc)


class UdpSession:
    """One UDP command session with one lamp"""

    def __init__(
        self, host: str, port: int | None = None, *, timeout: float = 0.5
    ) -> None:
        self.host = host
        # Called when a frame got "illegal request": the lamp left direct mode
        self.on_illegal: Callable[[], None] | None = None
        # Called when a frame got a refused token: the session is gone
        self.on_refused: Callable[[], None] | None = None
        self._port = port
        # Wait for one reply, a command waits up to ATTEMPTS times this
        self._timeout = timeout
        self._transport: asyncio.DatagramTransport | None = None
        self._token: str | None = None
        self._ids = itertools.count(1)
        # The id of udp_sess_new for the current session. Replies to older ids
        # belong to an old session
        self._session_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._token_waiter: asyncio.Future[str] | None = None

    @property
    def is_open(self) -> bool:
        return self._token is not None

    async def open(self) -> None:
        """Start a new session, raises UdpUnavailable when the lamp doesn't answer"""
        self._token = None
        port = self._port if self._port is not None else UDP_PORT
        loop = asyncio.get_running_loop()
        if self._transport is None:
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _Protocol(self), remote_addr=(self.host, port)
                )
            except OSError as err:
                raise UdpUnavailable(
                    f"Can't open UDP to {self.host}:{port}: {err!r}"
                ) from err
            self._transport = transport
        waiter: asyncio.Future[str] = loop.create_future()
        self._token_waiter = waiter
        id = next(self._ids)
        try:
            token = await self._send_and_wait(
                {"id": id, "method": "udp_sess_new", "params": []}, waiter
            )
        except TimeoutError as err:
            raise UdpUnavailable(f"No UDP session from {self.host}:{port}") from err
        finally:
            self._token_waiter = None
        self._session_id = id
        self._token = token
        _LOGGER.debug("UDP session with %s opened", self.host)

    async def call(self, method: str, params: list) -> list:
        """Send a command in the session and return the result of the reply"""
        token = self._token
        if token is None:
            raise UdpUnavailable("No UDP session")
        id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[id] = future
        message = {"id": id, "method": method, "params": params, "token": token}
        try:
            reply = await self._send_and_wait(message, future)
        except TimeoutError as err:
            # A session that doesn't answer is gone
            self._drop_token(token)
            raise UdpUnavailable(f"No UDP reply to {method}") from err
        finally:
            self._pending.pop(id, None)
        error = reply.get("error")
        if error is None:
            return reply.get("result", [])
        if isinstance(error, dict) and error.get("code") == CODE_TOKEN_REFUSED:
            self._drop_token(token)
            raise TokenRefused(f"{method}: the lamp refused the session ({error})")
        raise LampError(f"{method} failed: {error}")

    def send_frame_cmd(self, params: list) -> None:
        """Send update_leds without waiting, the lamp answers only when it fails"""
        if self._token is None:
            raise UdpUnavailable("No UDP session")
        self._send(
            {
                "id": next(self._ids),
                "method": "update_leds",
                "params": params,
                "token": self._token,
            }
        )

    async def keep_alive(self) -> None:
        await self.call("udp_sess_keep_alive", KEEP_ALIVE_PARAMS)

    def close(self) -> None:
        """Close the socket, the lamp forgets the session by itself"""
        transport, self._transport = self._transport, None
        self._token = None
        self._fail_waiting(UdpUnavailable("UDP session closed"))
        if transport is not None:
            transport.close()

    async def _send_and_wait(self, message: dict, future: asyncio.Future) -> Any:
        for _ in range(ATTEMPTS):
            self._send(message)
            try:
                async with asyncio.timeout(self._timeout):
                    return await asyncio.shield(future)
            except TimeoutError:
                continue
        raise TimeoutError

    def _send(self, message: dict) -> None:
        data = json.dumps(message, separators=(",", ":")).encode() + b"\r\n"
        if len(data) > MAX_DATAGRAM:
            raise UdpTooLarge(
                f"{message['method']} is {len(data)} bytes, the lamp takes {MAX_DATAGRAM}"
            )
        if self._transport is None:
            raise UdpUnavailable("UDP session closed")
        self._transport.sendto(data)

    def _received(self, data: bytes) -> None:
        for line in data.split(b"\n"):
            if not line.strip():
                continue
            message = parse_reply(line.strip())
            if message is None:
                _LOGGER.debug("Bad UDP reply from %s: %r", self.host, line)
                continue
            self._handle(message)

    def _handle(self, message: dict[str, Any]) -> None:
        if message.get("method") == "udp_sess_token":
            token = (message.get("params") or {}).get("token")
            waiter = self._token_waiter
            if token and waiter is not None and not waiter.done():
                waiter.set_result(str(token))
            return
        id = message.get("id")
        future = self._pending.get(id)  # type: ignore[arg-type]
        if future is not None:
            if not future.done():
                future.set_result(message)
            return
        # Only a frame that failed gets a reply
        error = message.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if not isinstance(id, int) or id < self._session_id:
            return
        if code == CODE_ILLEGAL:
            self._call_back(self.on_illegal)
        elif code == CODE_TOKEN_REFUSED and self._token is not None:
            self._token = None
            self._call_back(self.on_refused)

    def _drop_token(self, token: str) -> None:
        # A new session may have been opened in the meantime
        if self._token == token:
            self._token = None

    def _failed(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error with %s: %r", self.host, exc)
        self._token = None
        self._fail_waiting(UdpUnavailable(f"UDP error: {exc!r}"))

    def _fail_waiting(self, error: Exception) -> None:
        waiters = [*self._pending.values(), self._token_waiter]
        for future in waiters:
            if future is not None and not future.done():
                future.set_exception(error)

    def _call_back(self, callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            _LOGGER.exception("Error in a UDP session handler")
