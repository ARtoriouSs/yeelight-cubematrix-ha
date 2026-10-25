"""Preset library: built-in arts and effects, user files and stored arts"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from .artformat import Art, ArtError, parse_text
from .effects import EFFECTS, Effect

Playable = Art | Effect
Source = Literal["builtin", "file", "stored"]

BUILTIN_DIR = Path(__file__).parent / "presets"


def load_dir(path: Path) -> tuple[dict[str, Art], list[str]]:
    """Load every *.yaml in a folder, a broken file is skipped and reported"""
    arts: dict[str, Art] = {}
    errors: list[str] = []
    if not path.is_dir():
        return arts, errors
    for file in sorted(path.glob("*.yaml")):
        try:
            for art in parse_text(file.read_text(encoding="utf-8")):
                arts[art.name] = art
        except (ArtError, OSError, UnicodeDecodeError) as err:
            errors.append(f"{file.name}: {err}")
    return arts, errors


def load_builtin() -> dict[str, Playable]:
    arts, errors = load_dir(BUILTIN_DIR)
    if errors:
        raise ArtError("; ".join(errors))
    return {**arts, **EFFECTS}


class PresetLibrary:
    """Lookup with priority stored > file > builtin"""

    def __init__(
        self,
        builtin: Mapping[str, Playable],
        files: Mapping[str, Art],
        stored: Mapping[str, Art],
    ) -> None:
        self._layers: tuple[tuple[Source, Mapping[str, Playable]], ...] = (
            ("stored", stored),
            ("file", files),
            ("builtin", builtin),
        )

    def get(self, name: str) -> Playable | None:
        for _, layer in self._layers:
            if name in layer:
                return layer[name]
        return None

    def source(self, name: str) -> Source | None:
        for source, layer in self._layers:
            if name in layer:
                return source
        return None

    def names(self) -> list[str]:
        return sorted({name for _, layer in self._layers for name in layer})
