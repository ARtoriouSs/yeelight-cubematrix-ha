"""A fake Cube Matrix on 127.0.0.x: the LAN protocol on TCP and the UDP session"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket

CLOSE_TIMEOUT = 2.0


def free_port(host: str = "127.0.0.2", kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def lamp_error(id: int, code: int, message: str, token: str | None) -> bytes:
    # Like the real lamp: the message has no quotes, so this is not valid JSON
    reply = f'{{"id":{id},"error":{{"code":{code}, "message":{message}}}'
    if token is not None:
        reply += f',"token":"{token}"'
    return (reply + "}\r\n").encode()


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, lamp: FakeLamp) -> None:
        self._lamp = lamp

    def datagram_received(self, data: bytes, addr) -> None:
        self._lamp._on_udp(data, addr)


class FakeLamp:
    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.port = 0
        self.udp_port = 0
        self.model = "CubeMatrix"
        self.power = "off"
        # Direct mode set over LAN
        self.direct = False
        # Method name -> error object sent back instead of a result
        self.errors: dict[str, dict] = {}
        # Methods that get no reply at all
        self.silent: set[str] = set()
        # No UDP replies at all, like a lamp without the session
        self.udp_silent = False
        # LAN methods
        self.calls: list[str] = []
        # UDP methods, frames not included
        self.udp_calls: list[str] = []
        # Every UDP message as it came in, frames included
        self.datagrams: list[dict] = []
        self.frames: list[str] = []
        # "udp" or "lan" for each recorded frame
        self.frame_sources: list[str] = []
        # Session token -> direct mode set in that session
        self.sessions: dict[str, bool] = {}
        self._server: asyncio.Server | None = None
        self._udp: asyncio.DatagramTransport | None = None
        self._lan_writers: list[asyncio.StreamWriter] = []
        self._tasks: set[asyncio.Task] = set()

    async def start(self, port: int | None = None) -> int:
        self._server = await asyncio.start_server(
            self._on_lan, self.ip, port if port is not None else self.port
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: _Udp(self), local_addr=(self.ip, self.udp_port)
        )
        self.udp_port = self._udp.get_extra_info("sockname")[1]
        return self.port

    async def stop(self) -> None:
        if self._udp is not None:
            self._udp.close()
        if self._server is not None:
            self._server.close()
        for writer in list(self._lan_writers):
            writer.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=CLOSE_TIMEOUT)
        if self._server is not None:
            await asyncio.wait_for(self._server.wait_closed(), CLOSE_TIMEOUT)

    def notify(self, params: dict) -> None:
        line = json.dumps({"method": "props", "params": params}) + "\r\n"
        for writer in self._lan_writers:
            writer.write(line.encode())

    def drop_direct(self) -> None:
        """Another app changed the colour: direct mode is gone and LAN is told"""
        self._leave_direct()
        self.notify({"color_mode": 1})

    def drop_direct_quietly(self) -> None:
        """What the real lamp does now and then over UDP: no message about it"""
        self._leave_direct()

    def forget_sessions(self) -> None:
        """Like a lamp restart: every token is refused from now on"""
        self.sessions.clear()

    def tokens(self) -> list[str | None]:
        """The token of every UDP message after the first one"""
        return [message.get("token") for message in self.datagrams[1:]]

    def _leave_direct(self) -> None:
        self.direct = False
        for token in self.sessions:
            self.sessions[token] = False

    async def _on_lan(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._tasks.add(asyncio.current_task())
        self._lan_writers.append(writer)
        try:
            while line := await reader.readline():
                reply = self._handle_lan(json.loads(line))
                if reply is not None:
                    writer.write((json.dumps(reply) + "\r\n").encode())
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            self._lan_writers.remove(writer)
            writer.close()
            self._tasks.discard(asyncio.current_task())

    def _handle_lan(self, message: dict) -> dict | None:
        method = message["method"]
        params = message["params"]
        if method == "update_leds":
            # No reply, and frames are ignored outside direct mode
            if self.direct and self.power == "on":
                self.frames.append(params[0])
                self.frame_sources.append("lan")
            return None
        self.calls.append(method)
        if method in self.silent:
            return None
        if method in self.errors:
            return {"id": message["id"], "error": self.errors[method]}
        result: list = ["ok"]
        if method == "get_prop":
            values = {"model": self.model, "power": self.power}
            result = [values.get(name, "") for name in params]
        elif method == "set_power":
            self._set_power(params[0])
        elif method == "activate_fx_mode":
            self.direct = params[0] == {"mode": "direct"}
        return {"id": message["id"], "result": result}

    def _set_power(self, power: str) -> None:
        self.power = power
        if power == "off":
            self._leave_direct()

    def _on_udp(self, data: bytes, addr) -> None:
        for line in data.split(b"\r\n"):
            if not line.strip():
                continue
            message = json.loads(line)
            self.datagrams.append(message)
            if self.udp_silent:
                continue
            reply = self._handle_udp(message)
            if reply is not None and self._udp is not None:
                self._udp.sendto(reply, addr)

    def _handle_udp(self, message: dict) -> bytes | None:
        method = message["method"]
        params = message["params"]
        id = message["id"]
        if method == "udp_sess_new":
            self.udp_calls.append(method)
            token = secrets.token_hex(16)
            self.sessions[token] = False
            reply = {"method": "udp_sess_token", "params": {"token": token}}
            return (json.dumps(reply) + "\r\n").encode()
        token = message.get("token")
        if token not in self.sessions:
            return lamp_error(id, -1, "invalid parameter", None)
        if method == "update_leds":
            if self.sessions[token] and self.power == "on":
                self.frames.append(params[0])
                self.frame_sources.append("udp")
                return None
            return lamp_error(id, 6, "illegal request", token)
        self.udp_calls.append(method)
        if method in self.silent:
            return None
        if method in self.errors:
            error = self.errors[method]
            return lamp_error(id, error["code"], error["message"], token)
        if method == "set_power":
            self._set_power(params[0])
        elif method == "activate_fx_mode":
            self.sessions[token] = params[0] == {"mode": "direct"}
        reply = {"id": id, "result": ["ok"], "token": token}
        return (json.dumps(reply) + "\r\n").encode()
