"""Форматы черновиков: длины и разбивка треда (ТЗ, этап 10, задача 3)."""

from __future__ import annotations

from dataclasses import dataclass, field

X_LIMIT = 280


@dataclass(frozen=True)
class FormatSpec:
    name: str
    max_len: int = X_LIMIT
    min_parts: int = 0
    max_parts: int = 0
    needs_target: bool = False


FORMATS: dict[str, FormatSpec] = {}


@dataclass(frozen=True)
class FormatCheck:
    """Что проверяется: формат, текст, части треда, целевой пост, длинный пост паспорта."""

    format: str
    text: str
    parts: tuple[str, ...] | list[str] = field(default_factory=tuple)
    reply_to_url: str | None = None
    has_target: bool = False
    long_max_len: int | None = None


def validate_format(draft: FormatCheck) -> list[str]:
    raise NotImplementedError


def split_thread(text: str, *, max_len: int = X_LIMIT) -> list[str]:
    raise NotImplementedError
