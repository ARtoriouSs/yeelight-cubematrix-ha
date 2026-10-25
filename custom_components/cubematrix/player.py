"""Player: background content, events on top of it and the way back"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from . import connection
from .artformat import RGB, Art, Frame, fit_frames
from .layout import Layout
from .presets import Playable, PresetLibrary
from .protocol import LampError

_LOGGER = logging.getLogger(__name__)

# How long a static image or an effect stays when the show gives no duration
DEFAULT_SHOW_S = 5.0
# Wait after a lamp error before trying the content again
RETRY_S = 5.0
# Fit mode for the background, a small art sits in the middle of a bigger canvas
BACKGROUND_FIT = "center"
# How often endless content checks the lamp. A still picture is sent again (a shorter
# STATIC_REFRESH_S wins), and UDP may be tried again after a failure
WATCHDOG_S = 30.0

THEN_WORDS = ("restore", "hold", "off")
# Internal: after an event that woke a cube that is off, the lamp goes off again
WAKE_OFF = "_wake_off"
Then = str


class Sink(Protocol):
    max_fps: float

    async def ensure_ready(self, refresh: bool = False, retry_udp: bool = False) -> None: ...

    async def send_frame(self, raw: bytes) -> None: ...

    async def power_off(self) -> None: ...


@dataclass(frozen=True)
class _Content:
    """A playable that is checked and fitted to the layout"""

    name: str
    playable: Playable
    # Fitted frames for art, None for an effect
    frames: tuple[Frame, ...] | None
    spots: tuple[RGB, ...] | None

    @property
    def is_static(self) -> bool:
        return self.frames is not None and len(self.frames) == 1


async def _done() -> None:
    return None


def _done_task() -> asyncio.Task:
    return asyncio.Task(_done(), eager_start=True)


class Player:
    def __init__(
        self,
        sink: Sink,
        layout: Layout,
        library: Callable[[], PresetLibrary],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._layout = layout
        self._library = library
        self._sleep = sleep
        self._clock = clock
        self.power = False
        self.brightness = 255
        self.background: str | None = "fire"
        self.playing: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._closed = False
        # What the content task is doing now
        self._in_event = False
        self._on_background = False
        # Set when the event part of the current show ends, the show() caller waits on it
        self._event_done: asyncio.Event | None = None
        # The frame on the cube while content holds still, sent again on a brightness change
        self._held: tuple[Frame, Sequence[RGB] | None] | None = None
        self._last: tuple[Frame, Sequence[RGB] | None] | None = None
        # Set after a lamp error until a frame goes out again, so the log is not flooded
        self._lamp_lost = False

    # Public API

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    async def turn_on(self) -> None:
        async with self._lock:
            running = self._task is not None and not self._task.done()
            if self._closed or (self.power and running):
                return
            self.power = True
            self._notify()
            await self._replace(self._run_background, background=True)

    async def turn_off(self) -> None:
        async with self._lock:
            if self._closed:
                return
            await self._cancel()
            changed = self.power or self.playing is not None
            self.power = False
            self.playing = None
            if changed:
                self._notify()
            await self._sink.power_off()

    async def set_brightness(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        async with self._lock:
            if self._closed or value == self.brightness:
                return
            self.brightness = value
            self._notify()
            held = self._held
            if self.power and held is not None:
                try:
                    await self._send(*held)
                except LampError as err:
                    _LOGGER.warning("Could not send the new brightness: %s", err)

    async def set_background(self, name: str | None) -> None:
        if name is not None:
            playable = self._library().get(name)
            if playable is None:
                raise KeyError(name)
            # Fails here, not later in the background task
            self._fit(playable, name, BACKGROUND_FIT)
        async with self._lock:
            if self._closed:
                return
            changed = name != self.background
            self.background = name
            if changed:
                self._notify()
            if not self.power or self._in_event:
                # An event goes back to the new background when it ends
                return
            if changed or not self._on_background:
                await self._replace(self._run_background, background=True)

    async def show(
        self,
        playable: Playable,
        *,
        name: str,
        repeat: int | None = 1,
        duration: float | None = None,
        fps: float | None = None,
        then: Then = "restore",
        fit: str = "error",
        wake: bool = False,
    ) -> asyncio.Task:
        """Play an event, the returned task ends when the event part ends.

        With wake, an event on a cube that is off still plays, and the lamp goes off again after it
        """
        # Every check comes first, so a bad show leaves the cube as it is
        content = self._fit(playable, name, fit)
        after: _Content | None = None
        if then not in THEN_WORDS:
            then_playable = self._library().get(then)
            if then_playable is None:
                raise KeyError(then)
            after = self._fit(then_playable, then, BACKGROUND_FIT)
        async with self._lock:
            if self._closed or not (self.power or wake):
                _LOGGER.debug("Light is off, %s is muted", name)
                return _done_task()
            # The light stays off, so after the event the lamp goes off again
            then = then if self.power else WAKE_OFF
            done = asyncio.Event()
            await self._replace(
                lambda: self._run_show(content, repeat, duration, fps, then, after, done),
                event=done,
            )
        return asyncio.create_task(done.wait())

    async def stop(self) -> None:
        """End the current show and go back to the background"""
        async with self._lock:
            if self._closed or not self.power or self._on_background:
                return
            await self._replace(self._run_background, background=True)

    async def shutdown(self) -> None:
        """Stop the content task for good, nothing goes to the sink after this"""
        self._closed = True
        self._listeners.clear()
        async with self._lock:
            await self._cancel()

    # Content task management

    async def _replace(
        self,
        factory: Callable[[], Awaitable[None]],
        *,
        background: bool = False,
        event: asyncio.Event | None = None,
    ) -> None:
        """Stop the running content and start new one, call with the lock held"""
        await self._cancel()
        # Set the flags now, the task may not run before the next call
        self._on_background = background
        self._in_event = event is not None
        self._event_done = event
        self._task = asyncio.create_task(self._guard(factory))

    async def _cancel(self) -> None:
        task, self._task = self._task, None
        try:
            if task is not None and not task.done():
                task.cancel()
                # asyncio.wait does not swallow a cancel of the caller itself
                await asyncio.wait({task})
        finally:
            # Runs even when the caller is cancelled, so no flag is left behind
            if self._event_done is not None:
                # Also covers a show that was replaced before its task started
                self._event_done.set()
                self._event_done = None
            self._in_event = False
            self._on_background = False
            self._held = None

    async def _guard(self, factory: Callable[[], Awaitable[None]]) -> None:
        # The coroutine is made here, so a task cancelled before it starts leaves nothing behind
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Player content stopped with an error")

    # Content

    async def _run_background(self) -> None:
        self._on_background = True
        name = self.background
        content = None
        if name is not None:
            playable = self._library().get(name)
            if playable is None:
                _LOGGER.warning("Background %s is gone, showing black", name)
            else:
                try:
                    content = self._fit(playable, name, BACKGROUND_FIT)
                except ValueError as err:
                    _LOGGER.warning("Background %s does not fit, showing black: %s", name, err)
        if content is None:
            await self._run_black()
        else:
            self._set_playing(content.name)
            await self._steady(lambda: self._loop(content), fallback=self._run_black)

    async def _run_black(self) -> None:
        self._set_playing(None)
        await self._steady(lambda: self._hold(self._black(), None, None, steady=True))

    async def _run_show(
        self,
        content: _Content,
        repeat: int | None,
        duration: float | None,
        fps: float | None,
        then: Then,
        after: _Content | None,
        done: asyncio.Event,
    ) -> None:
        self._set_playing(content.name)
        failed = False
        try:
            await self._sink.ensure_ready(refresh=True)
            await self._play_event(content, repeat, duration, fps)
        except LampError as err:
            # The moment for this event has passed, it is not played again
            if self._lamp_lost:
                _LOGGER.debug("Show %s failed: %s", content.name, err)
            else:
                _LOGGER.warning("Show %s failed: %s", content.name, err)
            self._lamp_lost = True
            failed = True
        except Exception:
            _LOGGER.exception("Show %s stopped with an error", content.name)
            failed = True
        finally:
            self._in_event = False
            done.set()
        if failed:
            await self._sleep(RETRY_S)

        if then == WAKE_OFF:
            self._set_playing(None)
            try:
                await self._sink.power_off()
            except LampError as err:
                _LOGGER.debug("Could not turn the lamp off after %s: %s", content.name, err)
        elif then == "restore":
            await self._run_background()
        elif then == "hold":
            last = self._last
            if last is not None:
                await self._steady(
                    lambda: self._hold(last[0], last[1], None, steady=True),
                    fallback=self._run_background,
                )
            else:
                await self._forever()
        elif then == "off":
            await self._run_black()
        else:
            assert after is not None
            self._set_playing(after.name)
            await self._steady(lambda: self._loop(after), fallback=self._run_background)

    async def _play_event(
        self,
        content: _Content,
        repeat: int | None,
        duration: float | None,
        fps: float | None,
    ) -> None:
        if content.is_static:
            if duration is None and repeat is not None:
                duration = DEFAULT_SHOW_S
            assert content.frames is not None
            await self._hold(content.frames[0], content.spots, duration)
            return
        if content.frames is None and duration is None and repeat is not None:
            # An effect has no loops to count, so it runs for the default time
            duration = DEFAULT_SHOW_S
        until = None if duration is None else self._clock() + duration
        await self._animate(self._frames(content, repeat), until, fps or content.playable.fps)

    async def _loop(self, content: _Content) -> None:
        """Play content until cancelled"""
        if content.is_static:
            assert content.frames is not None
            await self._hold(content.frames[0], content.spots, None, steady=True)
        else:
            frames = self._frames(content, None)
            await self._animate(frames, None, content.playable.fps, steady=True)

    async def _steady(
        self,
        play: Callable[[], Awaitable[None]],
        fallback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Run endless content, after a lamp error wait and start it again

        After any other error, wait and run fallback instead (with no fallback the error goes up)
        """
        while True:
            try:
                await play()
                return
            except LampError as err:
                if self._lamp_lost:
                    _LOGGER.debug("Lamp still not reachable: %s", err)
                else:
                    _LOGGER.warning(
                        "Lamp error, trying again every %s s (repeats are logged at debug level): %s",
                        RETRY_S,
                        err,
                    )
                self._lamp_lost = True
                await self._sleep(RETRY_S)
            except Exception:
                if fallback is None:
                    raise
                _LOGGER.exception("Player content stopped with an error, going back to the background")
                await self._sleep(RETRY_S)
                await fallback()
                return

    def _frames(
        self, content: _Content, loops: int | None
    ) -> Iterator[tuple[Frame, Sequence[RGB] | None, float | None]]:
        """Yield (frame, spots, seconds) for each frame to show, None seconds means use fps"""
        playable = content.playable
        if content.frames is None:
            width, height = self._layout.width, self._layout.height
            # An effect has no loops, the caller ends it by time
            for index in itertools.count():
                yield playable.frame(index, width, height), None, None
            return
        assert isinstance(playable, Art)
        for loop in itertools.count():
            if loops is not None and loop >= loops:
                return
            for frame, seconds in zip(content.frames, playable.durations):
                yield frame, content.spots, seconds

    async def _animate(
        self,
        frames: Iterator[tuple[Frame, Sequence[RGB] | None, float | None]],
        until: float | None,
        fps: float,
        *,
        steady: bool = False,
    ) -> None:
        next_at = self._clock()
        check_at = next_at + WATCHDOG_S
        for frame, spots, seconds in frames:
            if until is not None and self._clock() >= until:
                return
            if steady and self._clock() >= check_at:
                # Gives UDP another chance after it failed
                await self._sink.ensure_ready(retry_udp=True)
                check_at = self._clock() + WATCHDOG_S
            await self._send(frame, spots)
            # Read after the send, the sink may have just switched to UDP
            max_fps = self._sink.max_fps
            if seconds is None:
                seconds = 1 / min(fps, max_fps)
            else:
                seconds = max(seconds, 1 / max_fps)
            next_at += seconds
            wake = next_at if until is None else min(next_at, until)
            delay = wake - self._clock()
            if delay > 0:
                await self._sleep(delay)
            else:
                # Running late, do not try to catch up
                next_at = self._clock()

    async def _hold(
        self,
        frame: Frame,
        spots: Sequence[RGB] | None,
        seconds: float | None,
        *,
        steady: bool = False,
    ) -> None:
        """Show one frame for seconds (None is forever)

        The frame is sent again every STATIC_REFRESH_S or WATCHDOG_S, whichever is shorter,
        and every WATCHDOG_S the lamp is checked first
        """
        until = None if seconds is None else self._clock() + seconds
        check_at = self._clock() + WATCHDOG_S
        try:
            while True:
                await self._send(frame, spots)
                self._held = (frame, spots)
                refresh = connection.STATIC_REFRESH_S
                if refresh is None or refresh > WATCHDOG_S:
                    refresh = WATCHDOG_S
                left = None if until is None else until - self._clock()
                if left is not None and left <= 0:
                    return
                if left is not None and left <= refresh:
                    await self._sleep(left)
                    return
                await self._sleep(refresh)
                if self._clock() >= check_at - 1e-6:
                    # Puts back direct mode after a lamp restart or a colour change from
                    # another app, and finds out when the lamp is gone
                    await self._sink.ensure_ready(refresh=True, retry_udp=steady)
                    check_at = self._clock() + WATCHDOG_S
        finally:
            self._held = None

    async def _send(self, frame: Frame, spots: Sequence[RGB] | None) -> None:
        self._last = (frame, spots)
        await self._sink.send_frame(self._layout.led_bytes(frame, spots, self.brightness))
        if self._lamp_lost:
            self._lamp_lost = False
            _LOGGER.info("Lamp is reachable again")

    # Helpers

    def _fit(self, playable: Playable, name: str, fit: str) -> _Content:
        if isinstance(playable, Art):
            frames = fit_frames(playable, self._layout.width, self._layout.height, fit)  # type: ignore[arg-type]
            return _Content(name, playable, frames, playable.spots)
        return _Content(name, playable, None, None)

    def _black(self) -> Frame:
        return ((0, 0, 0),) * (self._layout.width * self._layout.height)

    @staticmethod
    async def _forever() -> None:
        await asyncio.get_running_loop().create_future()

    def _set_playing(self, name: str | None) -> None:
        if name != self.playing:
            self.playing = name
            self._notify()

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                _LOGGER.exception("Player listener failed")
