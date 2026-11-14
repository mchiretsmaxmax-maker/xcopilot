"""Статья по ссылке (ТЗ v3.1, этап 7): загрузка через `safe_get` и основной текст."""

from __future__ import annotations

from xcopilot.ingest.net import Getter, safe_get
from xcopilot.ingest.parsers.base import ParsedDoc

MAX_ARTICLE_BYTES = 2 * 1024 * 1024


async def fetch_article(url: str, *, get: Getter = safe_get) -> ParsedDoc:
    raise NotImplementedError
