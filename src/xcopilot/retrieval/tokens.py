"""Оценка числа токенов без сети (консервативная: лучше завысить)."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    raise NotImplementedError
