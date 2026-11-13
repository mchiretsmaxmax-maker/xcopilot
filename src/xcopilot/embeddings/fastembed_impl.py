"""Локальный эмбеддер на fastembed (onnxruntime)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class FastEmbedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, dim: int = 384) -> None:
        self.model_name = model_name
        self.dim = dim
        self._impl: Any = None

    def _model(self) -> Any:
        if self._impl is None:
            from fastembed import TextEmbedding

            self._impl = TextEmbedding(model_name=self.model_name)
        return self._impl

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [
            [float(x) for x in v] for v in self._model().embed(list(texts), batch_size=len(texts))
        ]
        for v in vectors:
            if len(v) != self.dim:
                raise ValueError(f"размерность {len(v)} != {self.dim}")
        return vectors
