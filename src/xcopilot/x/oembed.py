"""Чтение поста по ссылке через oEmbed (без ключей, бесплатно).

Текст, автор и дата берутся из HTML-фрагмента. Ссылки t.co и медиа (pic.x.com) вырезаются.
Длинные посты oEmbed обрезает — тогда `possibly_truncated=True` и бот просит прислать текст.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef, XPost

OEMBED_URL = "https://publish.x.com/oembed"
_MEDIA_PREFIXES = ("pic.twitter.com/", "pic.x.com/")
_TCO_PREFIXES = ("https://t.co/", "http://t.co/")
_ELLIPSES = ("…", "...")


class _TweetHtml(HTMLParser):
    """Собирает текст из <p> блока twitter-tweet и подпись-дату последней ссылки вне <p>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.date_text: str | None = None
        self._in_p = False
        self._p_done = False
        self._a_href: str | None = None
        self._a_text: list[str] = []
        self._in_a = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p" and not self._p_done:
            self._in_p = True
        elif tag == "br" and self._in_p:
            self.parts.append("\n")
        elif tag == "a":
            self._in_a = True
            self._a_href = dict(attrs).get("href") or ""
            self._a_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._in_p:
            self._in_p = False
            self._p_done = True
        elif tag == "a" and self._in_a:
            self._in_a = False
            text = "".join(self._a_text)
            if self._in_p:
                if not _is_junk_link(self._a_href or "", text):
                    self.parts.append(text)
            elif self._p_done:
                self.date_text = text.strip()

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._a_text.append(data)
        elif self._in_p:
            self.parts.append(data)


def _is_junk_link(href: str, text: str) -> bool:
    t = text.strip()
    if t.startswith(_MEDIA_PREFIXES):
        return True
    host = urlsplit(href).netloc.lower()
    return host == "t.co" and t.startswith(_TCO_PREFIXES)


def _clean(parts: list[str]) -> str:
    raw = "".join(parts)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in raw.split("\n")]
    return "\n".join(lines).strip()


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%B %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def _handle_from_url(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    path = urlsplit(url).path.strip("/")
    return path.split("/")[0] or None


def parse_oembed(ref: PostRef, data: dict[str, Any]) -> XPost:
    """Пост из JSON-ответа oEmbed."""
    html = data.get("html")
    if not isinstance(html, str) or not html:
        raise XUnavailable(f"oEmbed: пустой ответ для поста {ref.id}")
    parser = _TweetHtml()
    parser.feed(html)
    parser.close()
    text = _clean(parser.parts)
    author_name = data.get("author_name")
    return XPost(
        id=ref.id,
        text=text,
        author_handle=_handle_from_url(data.get("author_url")) or ref.handle,
        author_name=author_name if isinstance(author_name, str) else None,
        created_at=_parse_date(parser.date_text),
        source="oembed",
        possibly_truncated=text.endswith(_ELLIPSES),
    )


class OEmbedReader:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self, ref: PostRef) -> XPost:
        """Пост по ссылке; XNotFound — нет/удалён/приватный, XUnavailable — сбой сервиса."""
        params = {"url": ref.url, "omit_script": "1", "dnt": "true", "hide_thread": "1"}
        try:
            resp = await self._http.get(OEMBED_URL, params=params)
        except httpx.HTTPError as exc:
            raise XUnavailable(f"oEmbed недоступен: {type(exc).__name__}") from None
        if resp.status_code in (403, 404):
            raise XNotFound(f"пост {ref.id} не найден или закрыт (oEmbed {resp.status_code})")
        if resp.status_code != 200:
            raise XUnavailable(f"oEmbed вернул {resp.status_code} для поста {ref.id}")
        try:
            data = resp.json()
        except ValueError:
            raise XUnavailable(f"oEmbed: не JSON для поста {ref.id}") from None
        if not isinstance(data, dict):
            raise XUnavailable(f"oEmbed: неожиданный ответ для поста {ref.id}")
        return parse_oembed(ref, data)
