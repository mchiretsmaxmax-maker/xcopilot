"""Длины вариантов ответа и цитаты: короткий / средний / длинный (решение владельца 19.11.2026).

Ответ и цитата дают три варианта разной длины: по одному на длину. Полосы — в
`config/thresholds.toml` [reply_lengths]; вариант вне своей полосы (с допуском) уходит на
переписывание с замечанием «до N знаков». Один вариант (n=1) длины не получает.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

SLOTS: tuple[str, ...] = ("short", "medium", "long")
LENGTH_FORMATS: tuple[str, ...] = ("reply", "quote")
LABELS: dict[str, str] = {"short": "🩳 коротко", "medium": "💬 средне", "long": "📜 длинно"}
CONFIG = Path(__file__).resolve().parents[3] / "config" / "thresholds.toml"


@dataclass(frozen=True)
class LengthBands:
    short_max: int = 70
    medium_max: int = 140
    long_max: int = 220
    tolerance: int = 10

    @classmethod
    def load(cls, path: Path | None = None) -> LengthBands:
        """Полосы из `config/thresholds.toml` [reply_lengths]; нет файла — по умолчанию."""
        path = path or CONFIG
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8")).get("reply_lengths", {})
        known = {k: int(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class LengthIssue:
    kind: str  # "too_long" | "too_short"
    target: int  # граница, к которой привести
    size: int


def assign_slots(n: int) -> list[str]:
    """Длины n вариантов по порядку: 3 → короткий, средний, длинный; 1 — без длины."""
    if n <= 1:
        return []
    if n == 2:
        return ["short", "long"]
    return [SLOTS[i % len(SLOTS)] for i in range(n)]


def bounds(slot: str, bands: LengthBands) -> tuple[int, int]:
    """Полоса длины (от, до) в знаках."""
    if slot == "short":
        return 0, bands.short_max
    if slot == "medium":
        return bands.short_max, bands.medium_max
    return bands.medium_max, bands.long_max


def length_issue(slot: str, size: int, bands: LengthBands) -> LengthIssue | None:
    """Вне полосы с допуском → что исправить; в полосе — None."""
    if slot not in SLOTS:
        return None
    lo, hi = bounds(slot, bands)
    if size > hi + bands.tolerance:
        return LengthIssue("too_long", hi, size)
    if lo and size < lo - bands.tolerance:
        return LengthIssue("too_short", lo, size)
    return None


def label(slot: str | None) -> str:
    return LABELS.get(slot or "", "")


_DESCRIPTION = {
    "short": "короткий: одна фраза, живая реакция, до {hi} знаков",
    "medium": "средний: {lo}–{hi} знаков, одна мысль в одно-два предложения",
    "long": "длинный: {lo}–{hi} знаков, развёрнутая реакция или свой опыт, но без воды",
}


def render_lengths(slots: list[str], bands: LengthBands) -> str:
    """Раздел задачи: длина каждого варианта по порядку (пусто — длины не заданы)."""
    if not slots:
        return ""
    lines = ["# Длина вариантов (строго, по одному варианту на длину)"]
    for i, slot in enumerate(slots, 1):
        lo, hi = bounds(slot, bands)
        lines.append(f"Вариант {i} — " + _DESCRIPTION[slot].format(lo=lo, hi=hi))
    lines.append("Не выходи за свою длину: короткий не растягивай, длинный не режь до реакции.")
    return "\n".join(lines)
