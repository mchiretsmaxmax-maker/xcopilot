"""Короткие ссылки X (t.co): раскрытие по редиректу и «в тексте только ссылки».

Раскрываем только t.co (без перехода по чужим адресам): HEAD без автоследования, адрес из
Location. Пост, у которого текст — одни t.co (картинка, видео), писателю не годится: там нет слов.
"""

from __future__ import annotations

import re

import httpx
import structlog

log = structlog.get_logger()

TIMEOUT = 5.0
MAX_HOPS = 3
_TCO = re.compile(r"(?<![\w.])(?:https?://)?t\.co/[A-Za-z0-9]{1,30}\b", re.IGNORECASE)
_TCO_URL = re.compile(r"^https://t\.co/[A-Za-z0-9]{1,30}$", re.IGNORECASE)


def find_short_link(text: str) -> str | None:
    """Первая ссылка t.co в тексте (с https://) или None."""
    match = _TCO.search(text)
    if match is None:
        return None
    link = match.group(0)
    return link if link.lower().startswith("http") else f"https://{link}"


def only_short_links(text: str) -> bool:
    """В тексте нет ничего, кроме ссылок t.co (пост — только картинка или видео)."""
    return bool(text.strip()) and not _TCO.sub("", text).strip()


async def expand_short_link(http: httpx.AsyncClient, url: str) -> str | None:
    """Адрес, на который ведёт t.co; не t.co, нет редиректа или сбой сети — None."""
    current = url.replace("http://", "https://", 1)
    if not _TCO_URL.match(current):
        return None  # чужие адреса не открываем
    for _ in range(MAX_HOPS):
        if not _TCO_URL.match(current):
            return current
        try:
            resp = await http.head(current, follow_redirects=False, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            log.info("x.short_link_failed", error=type(exc).__name__)
            return None
        location = resp.headers.get("location")
        if not resp.is_redirect or not location:
            return None
        current = location
    return None if _TCO_URL.match(current) else current
