"""Оценка числа токенов без сети (консервативная: лучше завысить)."""

from __future__ import annotations

import math

__all__ = ["estimate_tokens"]


def estimate_tokens(text: str) -> int:
    """Консервативно оценивает число токенов в тексте.

    Пустая строка даёт 0. Иначе возвращается максимум из единицы,
    оценки по трём знакам на токен и числа слов.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 3), len(text.split()))
