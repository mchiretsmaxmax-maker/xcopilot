"""Разбор правки владельца: сокращение, удалённая фраза, замена слова, регистр, перестройка."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditSummary:
    kinds: frozenset[str] = frozenset()
    removed: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()
    removed_at_start: bool = False
    length_ratio: float = 1.0
    casing: str | None = None


def classify_edit(before: str, after: str) -> EditSummary:
    raise NotImplementedError
