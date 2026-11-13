"""Нарезка длинного текста на куски около max_tokens с перекрытием, по абзацам."""

from __future__ import annotations


def count_tokens(text: str) -> int:
    raise NotImplementedError


def chunk_spans(text: str, max_tokens: int = 400, overlap: int = 50) -> list[tuple[int, int]]:
    raise NotImplementedError


def chunk(text: str, max_tokens: int = 400, overlap: int = 50) -> list[str]:
    raise NotImplementedError
