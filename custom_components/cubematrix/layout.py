"""Canvas layout and LED encoding for chained Cube Matrix modules."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .artformat import RGB, Frame

MODULE_SIZE = 5
SPOT_SLOTS = 5

# Placeholders, confirmed on real hardware
MODULE_MAJOR: Literal["row", "column"] = "row"
MODULE_SERPENTINE = False

ModuleKind = Literal["matrix", "spot"]


def module_led_order(
    major: Literal["row", "column"], serpentine: bool
) -> tuple[tuple[int, int], ...]:
    """Return the pixel (x, y) of each LED index in one module

    LED 0 is the bottom-right pixel, x counts from the left, y from the top
    """
    order = []
    for line in range(MODULE_SIZE):
        for step in range(MODULE_SIZE):
            if serpentine and line % 2 == 1:
                step_pos = MODULE_SIZE - 1 - step
            else:
                step_pos = step
            # Both axes start at the bottom-right corner
            line_pos = MODULE_SIZE - 1 - line
            along = MODULE_SIZE - 1 - step_pos
            if major == "row":
                order.append((along, line_pos))
            else:
                order.append((line_pos, along))
    return tuple(order)


# One table for the whole integration, so a hardware check changes only the constants
_LED_ORDER = module_led_order(MODULE_MAJOR, MODULE_SERPENTINE)


def parse_modules(text: str) -> tuple[ModuleKind, ...]:
    names = [name.strip().lower() for name in text.split(",")]
    for name in names:
        if name not in ("matrix", "spot"):
            raise ValueError(f"Unknown module: {name!r}")
    if "matrix" not in names:
        raise ValueError("At least one matrix module is needed")
    return tuple(names)  # type: ignore[arg-type]


def encode_leds(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@dataclass(frozen=True)
class Layout:
    modules: tuple[ModuleKind, ...] = ("matrix",)
    direction: Literal["horizontal", "vertical"] = "horizontal"

    @property
    def matrix_count(self) -> int:
        return self.modules.count("matrix")

    @property
    def spot_count(self) -> int:
        return self.modules.count("spot")

    @property
    def width(self) -> int:
        count = self.matrix_count if self.direction == "horizontal" else 1
        return MODULE_SIZE * count

    @property
    def height(self) -> int:
        count = self.matrix_count if self.direction == "vertical" else 1
        return MODULE_SIZE * count

    def led_bytes(
        self,
        frame: Frame,
        spots: Sequence[RGB] | None = None,
        brightness: int = 255,
    ) -> bytes:
        """Encode a canvas frame as raw LED bytes in chain order"""
        spots = list(spots or ())[: self.spot_count]
        if len(spots) < self.spot_count:
            # Missing spot colours get the average canvas colour
            spots += [self._average(frame)] * (self.spot_count - len(spots))
        raw = bytearray()
        matrix_index = 0
        spot_index = 0
        for kind in self.modules:
            if kind == "spot":
                raw += self._scaled(spots[spot_index], brightness) * SPOT_SLOTS
                spot_index += 1
                continue
            # The first matrix is the leftmost or the bottom one
            if self.direction == "horizontal":
                offset_x = matrix_index * MODULE_SIZE
                offset_y = 0
            else:
                offset_x = 0
                offset_y = (self.matrix_count - 1 - matrix_index) * MODULE_SIZE
            for x, y in _LED_ORDER:
                colour = frame[(offset_y + y) * self.width + offset_x + x]
                raw += self._scaled(colour, brightness)
            matrix_index += 1
        return bytes(raw)

    @staticmethod
    def _scaled(colour: RGB, brightness: int) -> bytes:
        return bytes(round(value * brightness / 255) for value in colour)

    @staticmethod
    def _average(frame: Frame) -> RGB:
        count = len(frame)
        return tuple(  # type: ignore[return-value]
            round(sum(pixel[channel] for pixel in frame) / count)
            for channel in range(3)
        )
