"""Парсер: text."""

from __future__ import annotations

from xcopilot.ingest.parsers.base import ParsedDoc


def parse(data: bytes) -> list[ParsedDoc]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1251")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

    if text.startswith("\ufeff"):
        text = text[1:]

    return [ParsedDoc(text=text, meta={})]
