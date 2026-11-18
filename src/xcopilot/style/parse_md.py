"""Разбор паспорта стиля из .md владельца (заглушка этапа: тесты сначала)."""

from __future__ import annotations

from dataclasses import dataclass, field

from xcopilot.style.errors import StyleError
from xcopilot.style.passport import StylePassport


class PassportParseError(StyleError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"Строка {line}: {message}")
        self.line = line


@dataclass
class ParseResult:
    passport: StylePassport
    warnings: list[str] = field(default_factory=list)


def parse_md(text: str, base: StylePassport | None = None) -> ParseResult:
    raise NotImplementedError
