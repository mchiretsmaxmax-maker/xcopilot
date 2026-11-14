"""Статья по ссылке (ТЗ v3.1, этап 7): загрузка через `safe_get` и основной текст.

Основной текст выделяет trafilatura (меню, реклама, подвал отбрасываются). Предел
страницы — 2 МБ (`MAX_ARTICLE_BYTES`); всё, что дальше, — обычный конвейер ингеста.
"""

from __future__ import annotations

import asyncio
import re

import trafilatura

from xcopilot.ingest.errors import ParseError
from xcopilot.ingest.net import Getter, safe_get
from xcopilot.ingest.parsers.base import ParsedDoc

MAX_ARTICLE_BYTES = 2 * 1024 * 1024
_CHARSET = re.compile(r"charset=([\w-]+)", re.I)
_SITE_SUFFIX = re.compile(r"\s+[|·—–-]\s+[^|·—–-]{1,60}$")


class ArticleEmpty(ParseError):
    """На странице нет основного текста."""


def _decode(body: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type)
    encoding = match.group(1) if match else "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def extract_main(html: str, url: str) -> tuple[str | None, str]:
    """(заголовок, основной текст) страницы; текст пустой, если выделить нечего."""
    doc = trafilatura.bare_extraction(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        with_metadata=True,
        favor_precision=True,
    )
    if doc is None:
        return None, ""
    text = (getattr(doc, "text", None) or "").strip()
    title = getattr(doc, "title", None)
    if isinstance(title, str) and title.strip():
        title = _SITE_SUFFIX.sub("", title.strip())
    else:
        title = None
    if title and text.startswith(title):
        text = text[len(title) :].lstrip()
    return title, text


async def fetch_article(url: str, *, get: Getter = safe_get) -> ParsedDoc:
    """Загрузить статью и вернуть основной текст с метаданными (title, words, url)."""
    fetched = await get(url, MAX_ARTICLE_BYTES)
    html = _decode(fetched.body, fetched.content_type)
    title, text = await asyncio.to_thread(extract_main, html, fetched.url)
    if not text:
        raise ArticleEmpty("На странице не нашёл текста статьи. Пришли текст сообщением.")
    return ParsedDoc(
        text=text, meta={"title": title, "words": len(text.split()), "url": fetched.url}
    )
