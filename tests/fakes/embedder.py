"""FakeEmbedder: детерминированные векторы (мешок слов в 384 корзины) и журнал пачек."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

REGISTRY_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass
class FakeEmbedder:
    model_name: str = REGISTRY_MODEL
    dim: int = 384
    calls: list[int] = field(default_factory=list)
    fail_next: int = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("FakeEmbedder: сбой по сценарию")
        self.calls.append(len(texts))
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"\w+", text.lower()) or [""]:
            h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
