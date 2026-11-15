"""Сходство текстов: rapidfuzz для вариантов и хвоста тренда, косинус эмбеддингов для тем.

Варианты одного запуска и копия чужого поста — это сходство слов, поэтому rapidfuzz;
повтор темы — сходство смысла, поэтому эмбеддинги (`topics.dedupe_vec`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process

VARIANTS_MAX = 0.8
TAIL_MAX = 0.6
TOPIC_MAX = 0.85


def text_similarity(a: str, b: str) -> float:
    """0..1: максимум из сходства по порядку символов и по набору слов (регистр не важен)."""
    if not a.strip() or not b.strip():
        return 0.0
    ratio = fuzz.ratio(a, b, processor=default_process)
    token_sort = fuzz.token_sort_ratio(a, b, processor=default_process)
    return max(ratio, token_sort) / 100.0


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def ensure_distinct(texts: Sequence[str], threshold: float = VARIANTS_MAX) -> list[int]:
    """Индексы вариантов, которые надо перегенерировать: из каждой похожей пары — поздний."""
    redo: list[int] = []
    for j in range(len(texts)):
        for i in range(j):
            if i not in redo and text_similarity(texts[i], texts[j]) >= threshold:
                redo.append(j)
                break
    return redo
