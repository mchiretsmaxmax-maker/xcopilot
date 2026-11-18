"""Детектор AI-шаблонов (no-ai-slop) — заглушка красной фазы."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SLOP = Path(__file__).resolve().parents[4] / "config" / "ai_slop.toml"


@dataclass(frozen=True)
class SlopHit:
    code: str
    fragment: str
    message: str


@dataclass(frozen=True)
class SlopConfig:
    pass


def load_slop_config(path: Path = SLOP) -> SlopConfig:
    raise NotImplementedError


def detect_slop(text: str, lang: str | None, config: SlopConfig | None = None) -> list[SlopHit]:
    return []
