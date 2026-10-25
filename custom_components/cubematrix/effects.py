"""Generated effects: frames computed from (index, width, height)"""

from __future__ import annotations

import colorsys
import math
import random
from typing import Protocol

from .artformat import RGB, Frame


class Effect(Protocol):
    name: str
    fps: float

    def frame(self, index: int, width: int, height: int) -> Frame: ...


def _byte(value: float) -> int:
    return max(0, min(255, round(value)))


def _hsv(hue: float, saturation: float, value: float) -> RGB:
    red, green, blue = colorsys.hsv_to_rgb(hue % 1.0, saturation, value)
    return (_byte(red * 255), _byte(green * 255), _byte(blue * 255))


class Rainbow:
    name = "rainbow"
    fps = 8.0

    def frame(self, index: int, width: int, height: int) -> Frame:
        # A diagonal band of hues that slowly moves
        span = width + height
        return tuple(
            _hsv((x + y) / span + index * 0.03, 1.0, 1.0)
            for y in range(height)
            for x in range(width)
        )


class Plasma:
    name = "plasma"
    fps = 8.0

    def frame(self, index: int, width: int, height: int) -> Frame:
        time = index * 0.25
        pixels = []
        for y in range(height):
            for x in range(width):
                # Sum of a few slow waves, scaled to 0..1
                wave = (
                    math.sin(x * 0.9 + time)
                    + math.sin(y * 0.8 - time * 0.7)
                    + math.sin((x + y) * 0.5 + time * 0.5)
                    + math.sin(math.hypot(x - width / 2, y - height / 2) * 0.9 - time)
                )
                level = (wave + 4) / 8
                pixels.append(_hsv(0.75 + level * 0.5, 1.0, 0.45 + 0.55 * level))
        return tuple(pixels)


# Heat 0..1 to colour: black, deep red, orange, yellow
_FIRE_STOPS: tuple[tuple[float, RGB], ...] = (
    (0.0, (0, 0, 0)),
    (0.35, (160, 10, 0)),
    (0.7, (255, 100, 0)),
    (1.0, (255, 210, 40)),
)


def _fire_color(heat: float) -> RGB:
    heat = max(0.0, min(1.0, heat))
    for (low, start), (high, end) in zip(_FIRE_STOPS, _FIRE_STOPS[1:]):
        if heat <= high:
            mix = (heat - low) / (high - low)
            return tuple(_byte(a + (b - a) * mix) for a, b in zip(start, end))  # type: ignore[return-value]
    return _FIRE_STOPS[-1][1]


class Fire:
    name = "fire"
    fps = 10.0

    def frame(self, index: int, width: int, height: int) -> Frame:
        rng = random.Random(index)
        # One flicker per column keeps neighbours alike, so it reads as flames, not noise
        columns = [rng.uniform(-0.15, 0.15) for _ in range(width)]
        pixels = []
        for y in range(height):
            base = (y + 0.5) / height  # 0 at the top, 1 at the bottom
            for x in range(width):
                wave = 0.12 * math.sin(index * 0.7 + x * 1.3 + y * 0.9)
                pixels.append(_fire_color(base**1.3 * 1.1 + columns[x] + wave - 0.1))
        return tuple(pixels)


EFFECTS: dict[str, Effect] = {
    effect.name: effect for effect in (Fire(), Rainbow(), Plasma())
}
