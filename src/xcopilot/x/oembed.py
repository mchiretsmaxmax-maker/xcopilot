"""Чтение поста по ссылке через oEmbed (без ключей, бесплатно).

Текст, автор и дата берутся из HTML-фрагмента. Ссылки t.co и медиа (pic.x.com) вырезаются.
Длинные посты oEmbed обрезает — тогда `possibly_truncated=True` и бот просит прислать текст.
"""

from __future__ import annotations

from typing import Any

import httpx

from xcopilot.x.models import PostRef, XPost

OEMBED_URL = "https://publish.x.com/oembed"


def parse_oembed(ref: PostRef, data: dict[str, Any]) -> XPost:
    """Пост из JSON-ответа oEmbed."""
    raise NotImplementedError


class OEmbedReader:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self, ref: PostRef) -> XPost:
        """Пост по ссылке; XNotFound — нет/удалён/приватный, XUnavailable — сбой сервиса."""
        raise NotImplementedError
