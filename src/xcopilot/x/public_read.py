"""Чтение публичного поста без ключа X (ADR 0031).

Сначала официальный oEmbed (текст и автор, без метрик). Если текст пуст, похож на обрезанный
или oEmbed не ответил — бесплатный api.fxtwitter.com (полный текст, автор, метрики).
Наружу уходит только адрес публичного поста. Таймаут 10 с, один повтор при сбое сети/сервиса.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef, XMetrics, XPost
from xcopilot.x.oembed import OEmbedReader

FXTWITTER_URL = "https://api.fxtwitter.com"
TIMEOUT = 10.0

log = structlog.get_logger()


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_fxtwitter(ref: PostRef, data: dict[str, Any]) -> XPost:
    """Пост из JSON fxtwitter. Метрики — только если источник их дал."""
    tweet = data.get("tweet")
    if not isinstance(tweet, dict):
        raise XUnavailable(f"fxtwitter: нет данных поста {ref.id}")
    text = tweet.get("text")
    if not isinstance(text, str) or not text.strip():
        raw = tweet.get("raw_text")
        text = raw.get("text") if isinstance(raw, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise XUnavailable(f"fxtwitter: пустой текст поста {ref.id}")
    author = tweet.get("author")
    author = author if isinstance(author, dict) else {}
    likes, reposts, replies = (_int(tweet.get(k)) for k in ("likes", "retweets", "replies"))
    metrics = None
    if likes is not None or reposts is not None or replies is not None:
        metrics = XMetrics(
            likes=likes or 0,
            reposts=reposts or 0,
            replies=replies or 0,
            quotes=_int(tweet.get("quotes")) or 0,
        )
    ts = _int(tweet.get("created_timestamp"))
    handle = author.get("screen_name")
    name = author.get("name")
    author_id = author.get("id")
    return XPost(
        id=ref.id,
        text=text.strip(),
        author_handle=handle if isinstance(handle, str) and handle else ref.handle,
        author_name=name if isinstance(name, str) else None,
        author_id=author_id if isinstance(author_id, str) else None,
        created_at=datetime.fromtimestamp(ts, UTC) if ts else None,
        source="fxtwitter",
        metrics=metrics,
    )


class PublicPostReader:
    def __init__(self, http: httpx.AsyncClient, *, timeout: float = TIMEOUT) -> None:
        self._http = http
        self._timeout = timeout
        self._oembed = OEmbedReader(http, timeout=timeout)

    async def get_post(self, ref: PostRef) -> XPost:
        """Пост по ссылке. XNotFound — удалён/закрыт; XUnavailable — оба источника недоступны."""
        oembed_error: XNotFound | XUnavailable | None = None
        post: XPost | None = None
        try:
            post = await self._retry(self._oembed.fetch, ref)
        except (XNotFound, XUnavailable) as exc:
            oembed_error = exc
        if post is not None and post.text and not post.possibly_truncated:
            log.info("x.public_read", via="oembed", post_id=ref.id)
            return post
        try:
            full = await self._retry(self._fxtwitter, ref)
        except XNotFound:
            raise
        except XUnavailable:
            if post is not None and post.text:
                log.info("x.public_read", via="oembed", post_id=ref.id, truncated=True)
                return post
            if isinstance(oembed_error, XNotFound):
                raise oembed_error from None
            raise
        log.info("x.public_read", via="fxtwitter", post_id=ref.id)
        return full

    fetch = get_post  # интерфейс PostFetcher для XService

    @staticmethod
    async def _retry(fn: Callable[[PostRef], Awaitable[XPost]], ref: PostRef) -> XPost:
        try:
            return await fn(ref)
        except XUnavailable:
            return await fn(ref)  # один повтор при сетевой ошибке / 5xx

    async def _fxtwitter(self, ref: PostRef) -> XPost:
        url = f"{FXTWITTER_URL}/{ref.handle or 'i'}/status/{ref.id}"
        try:
            resp = await self._http.get(url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise XUnavailable(f"fxtwitter недоступен: {type(exc).__name__}") from None
        if resp.status_code in (401, 403, 404):
            raise XNotFound(f"пост {ref.id} не найден или закрыт (fxtwitter {resp.status_code})")
        if resp.status_code != 200:
            raise XUnavailable(f"fxtwitter вернул {resp.status_code} для поста {ref.id}")
        try:
            data = resp.json()
        except ValueError:
            raise XUnavailable(f"fxtwitter: не JSON для поста {ref.id}") from None
        if not isinstance(data, dict):
            raise XUnavailable(f"fxtwitter: неожиданный ответ для поста {ref.id}")
        return parse_fxtwitter(ref, data)
