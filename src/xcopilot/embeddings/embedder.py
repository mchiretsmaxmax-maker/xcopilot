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
    raise NotImplementedError
