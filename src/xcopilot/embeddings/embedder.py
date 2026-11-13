"""Интерфейс эмбеддера: тексты → векторы фиксированной размерности."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

EMBED_BATCH = 32


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def batched[T](items: Sequence[T], size: int = EMBED_BATCH) -> list[list[T]]:
    if size < 1:
        raise ValueError(f"size должен быть >= 1, получено {size}")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]
