"""Pixel art format: parse art definitions and fit them to a cube layout"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import yaml

RGB = tuple[int, int, int]
Frame = tuple[RGB, ...]

OFF: RGB = (0, 0, 0)
DEFAULT_FPS = 5
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


class ArtError(ValueError):
    """Raised when an art definition is invalid"""


@dataclass(frozen=True)
class Art:
    name: str
    width: int
    height: int
    # Every frame is top-left first, row-major, length width * height
    frames: tuple[Frame, ...]
    # Seconds per frame, None means "use fps"
    durations: tuple[float | None, ...]
    fps: float
    loop: bool
    spots: tuple[RGB, ...] | None

    @property
    def is_static(self) -> bool:
        return len(self.frames) == 1


def parse_color(value: str | list[int] | bool) -> RGB:
    """Parse "#rrggbb", "off" or [r, g, b]"""
    # YAML reads an unquoted off as False
    if value is False:
        return OFF
    if isinstance(value, str):
        if value.lower() == "off":
            return OFF
        match = _HEX_RE.match(value)
        if match is None:
            raise ArtError(f"bad colour {value!r}, use '#rrggbb', 'off' or [r, g, b]")
        digits = match.group(1)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    if isinstance(value, (list, tuple)):
        if len(value) != 3 or not all(isinstance(part, int) and not isinstance(part, bool) for part in value):
            raise ArtError(f"bad colour {value!r}, expected three integers")
        if not all(0 <= part <= 255 for part in value):
            raise ArtError(f"bad colour {value!r}, values must be 0-255")
        return (value[0], value[1], value[2])
    raise ArtError(f"bad colour {value!r}")


def _parse_size(value: object, name: str) -> tuple[int, int]:
    match = _SIZE_RE.match(value) if isinstance(value, str) else None
    if match is None:
        raise ArtError(f"{name}: size must look like '5x5', got {value!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 1 or height < 1:
        raise ArtError(f"{name}: size must be at least 1x1, got {value!r}")
    return width, height


def _parse_palette(raw: object, name: str) -> dict[str, RGB]:
    palette: dict[str, RGB] = {".": OFF}
    if raw is None:
        return palette
    if not isinstance(raw, Mapping):
        raise ArtError(f"{name}: palette must be a mapping")
    for key, value in raw.items():
        if not isinstance(key, str) or len(key) != 1 or key.isspace():
            raise ArtError(f"{name}: palette key {key!r} must be a single character")
        try:
            palette[key] = parse_color(value)
        except ArtError as err:
            raise ArtError(f"{name}: palette key {key!r}: {err}") from None
    return palette


def _parse_row(
    row: object, palette: Mapping[str, RGB], width: int, where: str
) -> tuple[RGB, ...]:
    if isinstance(row, str):
        items: list[object] = list(re.sub(r"\s+", "", row))
    elif isinstance(row, list):
        items = row
    else:
        raise ArtError(f"{where}: row must be text or a list of colours")
    pixels = []
    for item in items:
        if isinstance(item, str) and len(item) == 1:
            if item not in palette:
                raise ArtError(f"{where}: unknown palette key {item!r}")
            pixels.append(palette[item])
        else:
            try:
                pixels.append(parse_color(item))  # type: ignore[arg-type]
            except ArtError as err:
                raise ArtError(f"{where}: {err}") from None
    if len(pixels) != width:
        raise ArtError(f"{where}: expected {width} pixels, got {len(pixels)}")
    return tuple(pixels)


def _parse_frame(
    raw: object, palette: Mapping[str, RGB], width: int, height: int, name: str, number: int
) -> Frame:
    if isinstance(raw, str):
        rows: list[object] = [line for line in raw.splitlines() if line.strip()]
    elif isinstance(raw, list):
        rows = raw
    else:
        raise ArtError(f"{name}: frame {number}: must be text or a list of rows")
    if len(rows) != height:
        raise ArtError(f"{name}: frame {number}: expected {height} rows, got {len(rows)}")
    pixels: list[RGB] = []
    for index, row in enumerate(rows, start=1):
        pixels.extend(_parse_row(row, palette, width, f"{name}: frame {number}, row {index}"))
    return tuple(pixels)


def parse_art(data: Mapping, *, default_name: str | None = None) -> Art:
    """Build an Art from a parsed definition"""
    if not isinstance(data, Mapping):
        raise ArtError("art definition must be a mapping")
    name = data.get("name", default_name)
    if not isinstance(name, str) or not name:
        raise ArtError("art needs a name")
    if "size" not in data:
        raise ArtError(f"{name}: size is required")
    width, height = _parse_size(data["size"], name)
    palette = _parse_palette(data.get("palette"), name)

    raw_frames = data.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise ArtError(f"{name}: frames must be a non-empty list")
    frames: list[Frame] = []
    durations: list[float | None] = []
    for number, raw in enumerate(raw_frames, start=1):
        duration = None
        if isinstance(raw, Mapping):
            if "pixels" not in raw:
                raise ArtError(f"{name}: frame {number}: mapping needs 'pixels'")
            duration = raw.get("duration")
            if duration is not None and (
                isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0
            ):
                raise ArtError(f"{name}: frame {number}: duration must be a positive number")
            raw = raw["pixels"]
        frames.append(_parse_frame(raw, palette, width, height, name, number))
        durations.append(None if duration is None else float(duration))

    fps = data.get("fps", DEFAULT_FPS)
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0:
        raise ArtError(f"{name}: fps must be a positive number")
    loop = data.get("loop", False)
    if not isinstance(loop, bool):
        raise ArtError(f"{name}: loop must be true or false")

    spots = data.get("spots")
    if spots is not None:
        if not isinstance(spots, list):
            raise ArtError(f"{name}: spots must be a list of colours")
        try:
            spots = tuple(parse_color(spot) for spot in spots)
        except ArtError as err:
            raise ArtError(f"{name}: spots: {err}") from None

    return Art(
        name=name,
        width=width,
        height=height,
        frames=tuple(frames),
        durations=tuple(durations),
        fps=fps,
        loop=loop,
        spots=spots,
    )


def parse_text(text: str) -> list[Art]:
    """Parse YAML holding one art mapping or a list of them"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ArtError(f"invalid YAML: {err}") from None
    if isinstance(data, Mapping):
        return [parse_art(data)]
    if isinstance(data, list) and data and all(isinstance(item, Mapping) for item in data):
        return [parse_art(item) for item in data]
    raise ArtError("expected an art mapping or a list of art mappings")


def fit_frames(
    art: Art, width: int, height: int, mode: Literal["error", "center", "tile"]
) -> tuple[Frame, ...]:
    """Place the art on a width x height canvas"""
    if (art.width, art.height) == (width, height):
        return art.frames
    if mode == "error" or art.width > width or art.height > height:
        raise ArtError(
            f"{art.name}: art is {art.width}x{art.height} but the layout is {width}x{height}"
        )
    if mode == "center":
        left = (width - art.width) // 2
        top = (height - art.height) // 2

        def source(x: int, y: int) -> RGB | None:
            ax, ay = x - left, y - top
            if 0 <= ax < art.width and 0 <= ay < art.height:
                return frame[ay * art.width + ax]
            return None
    elif mode == "tile":

        def source(x: int, y: int) -> RGB | None:
            return frame[(y % art.height) * art.width + x % art.width]
    else:
        raise ArtError(f"unknown fit mode {mode!r}")

    fitted = []
    for frame in art.frames:
        fitted.append(
            tuple(source(x, y) or OFF for y in range(height) for x in range(width))
        )
    return tuple(fitted)
