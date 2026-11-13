"""Локальный эмбеддер на fastembed (onnxruntime)."""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class FastEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = 384) -> None:
        self.model_name = model_name
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError
