"""Длины вариантов ответа и цитаты: короткий / средний / длинный (заглушка)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SLOTS: tuple[str, ...] = ("short", "medium", "long")
LENGTH_FORMATS: tuple[str, ...] = ("reply", "quote")
LABELS: dict[str, str] = {"short": "🩳 коротко", "medium": "💬 средне", "long": "📜 длинно"}


@dataclass(frozen=True)
class LengthBands:
    short_max: int = 70
    medium_max: int = 140
    long_max: int = 220
    tolerance: int = 10

    @classmethod
    def load(cls, path: Path | None = None) -> LengthBands:
        raise NotImplementedError


@dataclass(frozen=True)
class LengthIssue:
    kind: str  # "too_long" | "too_short"
    target: int  # граница, к которой привести
    size: int


def assign_slots(n: int) -> list[str]:
    raise NotImplementedError


def bounds(slot: str, bands: LengthBands) -> tuple[int, int]:
    raise NotImplementedError


def length_issue(slot: str, size: int, bands: LengthBands) -> LengthIssue | None:
    raise NotImplementedError


def label(slot: str | None) -> str:
    raise NotImplementedError


def render_lengths(slots: list[str], bands: LengthBands) -> str:
    raise NotImplementedError
