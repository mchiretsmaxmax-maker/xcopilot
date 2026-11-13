"""Парсер: text."""

from __future__ import annotations

from xcopilot.ingest.parsers.base import ParsedDoc


def parse(data: bytes) -> list[ParsedDoc]:
    raise NotImplementedError
