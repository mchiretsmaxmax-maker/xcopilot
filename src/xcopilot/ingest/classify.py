"""Определение типа входа: текст, ссылки на посты, хэндл, файл."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InputKind(StrEnum):
    TEXT = "text"
    LINKS = "links"
    HANDLE = "handle"
    FILE = "file"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class Incoming:
    """Что прислал пользователь: текст и/или файл (имя и заявленный размер)."""

    text: str | None = None
    file_name: str | None = None
    file_size: int | None = None


def classify(message: Incoming) -> InputKind:
    raise NotImplementedError
