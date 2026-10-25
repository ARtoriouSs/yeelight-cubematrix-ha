"""Yeelight LAN protocol: JSON commands and replies over TCP"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)

LAN_PORT = 55443
# How long to wait for a socket to close before giving up on it
CLOSE_TIMEOUT = 2.0


class LampError(Exception):
    """The lamp can't be reached, didn't answer or rejected a command"""


def command(id: int, method: str, params: list) -> bytes:
    message = {"id": id, "method": method, "params": params}
    return (json.dumps(message, separators=(",", ":")) + "\r\n").encode()


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), CLOSE_TIMEOUT)
    except (OSError, TimeoutError):
        pass


async def stop_task(task: asyncio.Task | None) -> None:
    if task is None or task is asyncio.current_task():
        return
    task.cancel()
    # wait() doesn't swallow a cancel of the task that called close()
    await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)


class LanClient:
    """One TCP connection to the lamp's LAN control port"""

    def __init__(self) -> None:
        self.on_notify: Callable[[dict], None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    async def connect(
        self, host: str, port: int | None = None, timeout: float = 5
    ) -> None:
        if port is None:
            port = LAN_PORT
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        except (OSError, TimeoutError) as err:
            raise LampError(f"Can't connect to {host}:{port}: {err!r}") from err
        self._writer = writer
        self._closed = False
        self._task = asyncio.get_running_loop().create_task(
            self._read_loop(reader), name=f"cubematrix LAN {host}"
        )

    async def call(self, method: str, params: list, timeout: float = 3) -> list:
        if self._closed or self._writer is None:
            raise LampError("Not connected to the lamp")
        id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[id] = future
        try:
            async with asyncio.timeout(timeout):
                self._writer.write(command(id, method, params))
                await self._writer.drain()
                reply = await future
        except TimeoutError as err:
            # A lamp that stops answering usually has a dead connection
            await self.close()
            raise LampError(f"No reply to {method} in {timeout} s") from err
        except OSError as err:
            await self.close()
            raise LampError(f"Can't send {method}: {err!r}") from err
        finally:
            self._pending.pop(id, None)
        if "error" in reply:
            raise LampError(f"{method} failed: {reply['error']}")
        return reply.get("result", [])

    def send(self, method: str, params: list) -> None:
        """Send a command that gets no reply, like update_leds"""
        if self._closed or self._writer is None or self._writer.is_closing():
            raise LampError("Not connected to the lamp")
        self._writer.write(command(next(self._ids), method, params))

    async def close(self) -> None:
        self._mark_closed()
        await stop_task(self._task)
        if self._writer is not None:
            await _close_writer(self._writer)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    _LOGGER.debug("Bad line from the lamp: %r", line)
                    continue
                if isinstance(message, dict):
                    self._handle(message)
        except (OSError, ValueError) as err:
            # ValueError: a line longer than the stream limit
            _LOGGER.debug("LAN connection failed: %r", err)
        finally:
            self._mark_closed()

    def _handle(self, message: dict[str, Any]) -> None:
        future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
        if future is not None:
            if not future.done():
                future.set_result(message)
            return
        if message.get("method") == "props" and self.on_notify is not None:
            try:
                self.on_notify(message.get("params") or {})
            except Exception:
                _LOGGER.exception("Error in the lamp notification handler")

    def _mark_closed(self) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(LampError("Connection to the lamp closed"))
        if self._writer is not None:
            self._writer.close()


async def get_props(host: str, names: list[str], timeout: float = 5) -> list:
    """Open a short LAN connection, read some props and close it again"""
    client = LanClient()
    await client.connect(host, timeout=timeout)
    try:
        return await client.call("get_prop", names)
    finally:
        await client.close()
