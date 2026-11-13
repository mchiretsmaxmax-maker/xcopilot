"""Парсеры входа: каждый возвращает список ParsedDoc."""

from __future__ import annotations

from pathlib import Path

from xcopilot.ingest.errors import ParseError
from xcopilot.ingest.parsers import docx, pdf, telegram_export, text
from xcopilot.ingest.parsers.base import ParsedDoc

SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf", ".docx", ".json")


def parse_file(name: str, data: bytes, *, owner_tg_id: int | None = None) -> list[ParsedDoc]:
    suffix = Path(name).suffix.lower()
    if suffix in (".txt", ".md"):
        return text.parse(data)
    if suffix == ".pdf":
        return pdf.parse(data)
    if suffix == ".docx":
        return docx.parse(data)
    if suffix == ".json":
        return telegram_export.parse(data, owner_tg_id)
    raise ParseError("Такой формат не беру: пришли txt, md, pdf, docx или result.json из Telegram.")


__all__ = ["SUPPORTED_EXTENSIONS", "ParsedDoc", "parse_file"]
