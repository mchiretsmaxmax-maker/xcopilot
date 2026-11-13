"""Парсеры входа: каждый возвращает список ParsedDoc."""

from __future__ import annotations

from xcopilot.ingest.parsers.base import ParsedDoc

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf", ".docx", ".json")


def parse_file(name: str, data: bytes, *, owner_tg_id: int | None = None) -> list[ParsedDoc]:
    raise NotImplementedError


__all__ = ["SUPPORTED_EXTENSIONS", "ParsedDoc", "parse_file"]
