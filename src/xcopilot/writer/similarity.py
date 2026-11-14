"""Сходство текстов: rapidfuzz для вариантов и хвоста тренда, косинус для тем."""

from __future__ import annotations

from collections.abc import Sequence

VARIANTS_MAX = 0.8
TAIL_MAX = 0.6
TOPIC_MAX = 0.85


def text_similarity(a: str, b: str) -> float:
    raise NotImplementedError


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    raise NotImplementedError


def ensure_distinct(texts: Sequence[str], threshold: float = VARIANTS_MAX) -> list[int]:
    raise NotImplementedError
