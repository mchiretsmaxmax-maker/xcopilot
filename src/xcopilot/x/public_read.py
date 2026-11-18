"""Чтение публичного поста без ключа X (ADR 0031).

Сначала официальный oEmbed (текст и автор, без метрик). Если текст пуст, похож на обрезанный
или oEmbed не ответил — бесплатный api.fxtwitter.com (полный текст, автор, метрики).
Текст oEmbed дополняется цифрами fxtwitter (лайки, ретвиты, ответы, просмотры, подписчики
автора) для автоисхода (ADR 0033); fxtwitter недоступен — пост без цифр.
Наружу уходит только адрес публичного поста. Таймаут 10 с, один повтор при сбое сети/сервиса.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef, XMetrics, XPost, XProfile
from xcopilot.x.oembed import OEmbedReader

FXTWITTER_URL = "https://api.fxtwitter.com"
FX_V2 = f"{FXTWITTER_URL}/2"
MIN_INTERVAL = 0.1  # пауза между запросами к FxEmbed: не больше 600 в минуту (лимит 1000)
MAX_PAGES = 5  # страниц cursor на одно листание
THREAD_MAX_CHARS = 8000  # тред длиннее — обрезаем по частям
TIMEOUT = 10.0
# fxtwitter стоит за Cloudflare: подпись python-httpx по умолчанию иногда получает 403.
HEADERS = {"User-Agent": "xcopilot/1.0"}

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
            bookmarks=_int(tweet.get("bookmarks")) or 0,
            impressions=_int(tweet.get("views")),
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
        author_followers=_int(author.get("followers")),
    )


class PublicPostReader:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout: float = TIMEOUT,
        min_interval: float = MIN_INTERVAL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._timeout = timeout
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._lock = asyncio.Lock()
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
            return await self._with_numbers(post, ref)
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

    async def fetch_thread(self, ref: PostRef) -> list[XPost]:
        """Тред автора по ссылке на любую его часть (/2/thread); одиночный пост — [пост]."""
        raise NotImplementedError

    async def fetch_target(self, ref: PostRef) -> XPost:
        """Пост с текстом всего треда; сбой /2/thread — обычный get_post."""
        raise NotImplementedError

    async def fetch_replies(self, post_id: str, limit: int = 30) -> list[XPost]:
        """Ответы под постом (/2/conversation, по лайкам), без ответов самого автора."""
        raise NotImplementedError

    async def fetch_quotes(self, post_id: str, limit: int = 30) -> list[XPost]:
        """Цитаты поста (/2/status/{id}/quotes), самые залайканные из MAX_PAGES страниц."""
        raise NotImplementedError

    async def fetch_profile(self, handle: str) -> XProfile:
        """Публичный профиль (/2/profile/{handle}). Пока нигде не используется."""
        raise NotImplementedError

    async def _with_numbers(self, post: XPost, ref: PostRef) -> XPost:
        """Текст oEmbed + публичные цифры fxtwitter; сбой fxtwitter — пост как есть."""
        try:
            full = await self._fxtwitter(ref)
        except (XNotFound, XUnavailable):
            return post
        return replace(post, metrics=full.metrics, author_followers=full.author_followers)

    @staticmethod
    async def _retry(fn: Callable[[PostRef], Awaitable[XPost]], ref: PostRef) -> XPost:
        try:
            return await fn(ref)
        except XUnavailable:
            return await fn(ref)  # один повтор при сетевой ошибке / 5xx

    async def _fxtwitter(self, ref: PostRef) -> XPost:
        url = f"{FXTWITTER_URL}/{ref.handle or 'i'}/status/{ref.id}"
        try:
            resp = await self._http.get(url, timeout=self._timeout, headers=HEADERS)
        except httpx.HTTPError as exc:
            raise XUnavailable(f"fxtwitter недоступен: {type(exc).__name__}") from None
        if resp.status_code in (401, 403):  # Cloudflare/доступ сервиса, а не «пост удалён»
            raise XUnavailable(f"fxtwitter отказал ({resp.status_code}) для поста {ref.id}")
        if resp.status_code == 404:
            raise XNotFound(f"пост {ref.id} не найден или закрыт (fxtwitter 404)")
        if resp.status_code != 200:
            raise XUnavailable(f"fxtwitter вернул {resp.status_code} для поста {ref.id}")
        try:
            data = resp.json()
        except ValueError:
            raise XUnavailable(f"fxtwitter: не JSON для поста {ref.id}") from None
        if not isinstance(data, dict):
            raise XUnavailable(f"fxtwitter: неожиданный ответ для поста {ref.id}")
        return parse_fxtwitter(ref, data)


def parse_status(obj: object, *, strip_mentions: bool = False) -> XPost | None:
    """Пост из объекта статуса FxEmbed v2; не статус (tombstone, пусто) — None."""
    raise NotImplementedError


def own_thread(focal_id: str, parts: list[XPost]) -> list[XPost]:
    """Части треда одного автора подряд вокруг поста focal_id (чужие предки отброшены)."""
    raise NotImplementedError


def thread_text(parts: list[XPost], *, max_chars: int = THREAD_MAX_CHARS) -> str:
    """Тред одним текстом: «1/N текст» через пустую строку; длинный — обрезан по частям."""
    raise NotImplementedError


@runtime_checkable
class TargetReader(Protocol):
    async def fetch_target(self, ref: PostRef) -> XPost: ...


async def read_target(reader: object, ref: PostRef) -> XPost:
    """Пост по ссылке для ответа/цитаты: у PublicPostReader — тред целиком, иначе fetch."""
    raise NotImplementedError
