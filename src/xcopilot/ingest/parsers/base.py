"""Общее для парсеров: результат разбора и лимиты."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 50_000


@dataclass(frozen=True, slots=True)
class ParsedDoc:
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
