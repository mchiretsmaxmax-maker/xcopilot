"""Парсер экспорта Telegram (result.json): только сообщения владельца."""

from __future__ import annotations

from xcopilot.ingest.parsers.base import ParsedDoc


def parse(data: bytes, owner_tg_id: int | None = None) -> list[ParsedDoc]:
    raise NotImplementedError
