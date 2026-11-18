"""Чтение публичного поста без ключа X (ADR 0031).

Сначала официальный oEmbed (текст и автор, без метрик). Если текст пуст, похож на обрезанный
или oEmbed не ответил — бесплатный api.fxtwitter.com v2 (полный текст, автор, метрики).
Текст oEmbed дополняется цифрами fxtwitter (лайки, ретвиты, ответы, просмотры, подписчики
автора) для автоисхода (ADR 0033); fxtwitter недоступен — пост без цифр.
Наружу уходит только адрес публичного поста. Таймаут 10 с, один повтор при сбое сети/сервиса.

FxEmbed v2 (docs/fxembed.md): тред (/2/thread), ответы (/2/conversation), цитаты
(/2/status/{id}/quotes), профиль (/2/profile). Между запросами пауза MIN_INTERVAL, листание
по cursor не дальше MAX_PAGES страниц.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.models import PostRef, XMedia, XMetrics, XPost, XProfile
from xcopilot.x.oembed import OEmbedReader

FXTWITTER_URL = "https://api.fxtwitter.com"
FX_V2 = f"{FXTWITTER_URL}/2"
MIN_INTERVAL = 0.1  # пауза между запросами к FxEmbed: не больше 600 в минуту (лимит 1000)
MAX_PAGES = 5  # страниц cursor на одно листание
PER_MINUTE = 300  # общий ограничитель клиента (лимит сервиса 1000/мин, запас под бота)
THREAD_MAX_CHARS = 8000  # тред длиннее — обрезаем по частям
TIMEOUT = 10.0
# fxtwitter стоит за Cloudflare: подпись python-httpx по умолчанию иногда получает 403.
HEADERS = {"User-Agent": "xcopilot/1.0"}

log = structlog.get_logger()


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


_LEADING_MENTIONS = re.compile(r"^(?:@\w+\s*)+")


def _media(obj: dict[str, Any]) -> tuple[XMedia, ...]:
    media = obj.get("media")
    if not isinstance(media, dict):
        return ()
    items = media.get("all")
    if not isinstance(items, list):
        items = [*(media.get("photos") or []), *(media.get("videos") or [])]
        external = media.get("external")
        if isinstance(external, dict):
            items.append(external)
    out: list[XMedia] = []
    for item in items:
        if isinstance(item, dict):
            kind, url = _str(item.get("type")), _str(item.get("url"))
            if kind and url:
                out.append(XMedia(type=kind, url=url))
    return tuple(out)


def _metrics(obj: dict[str, Any]) -> XMetrics | None:
    likes, replies = _int(obj.get("likes")), _int(obj.get("replies"))
    reposts = _int(obj.get("reposts"))
    if reposts is None:
        reposts = _int(obj.get("retweets"))  # v1
    if likes is None and reposts is None and replies is None:
        return None
    return XMetrics(
        likes=likes or 0,
        reposts=reposts or 0,
        replies=replies or 0,
        quotes=_int(obj.get("quotes")) or 0,
        bookmarks=_int(obj.get("bookmarks")) or 0,
        impressions=_int(obj.get("views")),
    )


def _build(
    obj: dict[str, Any], post_id: str, handle: str | None, *, strip_mentions: bool
) -> XPost | None:
    text = obj.get("text")
    if not isinstance(text, str) or not text.strip():
        raw = obj.get("raw_text")
        text = raw.get("text") if isinstance(raw, dict) else None
    if not isinstance(text, str):
        return None
    parent = obj.get("replying_to")
    parent = parent if isinstance(parent, dict) else {}
    if strip_mentions and parent:
        text = _LEADING_MENTIONS.sub("", text.strip())
    text = text.strip()
    if not text:
        return None
    author = obj.get("author")
    author = author if isinstance(author, dict) else {}
    ts = _int(obj.get("created_timestamp"))
    return XPost(
        id=post_id,
        text=text,
        author_handle=_str(author.get("screen_name")) or handle,
        author_name=_str(author.get("name")),
        author_id=_str(author.get("id")),
        created_at=datetime.fromtimestamp(ts, UTC) if ts else None,
        source="fxtwitter",
        metrics=_metrics(obj),
        author_followers=_int(author.get("followers")),
        in_reply_to_id=_str(parent.get("status")),
        lang=_str(obj.get("lang")),
        replying_to=_str(parent.get("screen_name")),
        media=_media(obj),
        is_note_tweet=obj.get("is_note_tweet") is True,
    )


def parse_status(obj: object, *, strip_mentions: bool = False) -> XPost | None:
    """Пост из объекта статуса FxEmbed v2; не статус (tombstone, пусто) — None.

    strip_mentions — убрать «@хэндл» в начале ответа (X дописывает их сам).
    """
    if not isinstance(obj, dict) or obj.get("type", "status") != "status":
        return None
    post_id = _str(obj.get("id"))
    if post_id is None:
        return None
    return _build(obj, post_id, None, strip_mentions=strip_mentions)


def parse_fxtwitter(ref: PostRef, data: dict[str, Any]) -> XPost:
    """Пост из JSON fxtwitter (v2 — ключ status, v1 — tweet). Метрики — если источник их дал."""
    obj = data.get("status")
    if not isinstance(obj, dict):
        obj = data.get("tweet")
    if not isinstance(obj, dict):
        raise XUnavailable(f"fxtwitter: нет данных поста {ref.id}")
    post = _build(obj, ref.id, ref.handle, strip_mentions=False)
    if post is None:
        raise XUnavailable(f"fxtwitter: пустой текст поста {ref.id}")
    return post


def _author_key(post: XPost) -> str | None:
    if post.author_id:
        return post.author_id
    return post.author_handle.lower() if post.author_handle else None


def own_thread(focal_id: str, parts: list[XPost]) -> list[XPost]:
    """Части треда одного автора подряд вокруг поста focal_id (чужие предки отброшены)."""
    index = next((i for i, p in enumerate(parts) if p.id == focal_id), None)
    if index is None:
        return []
    key = _author_key(parts[index])
    lo = hi = index
    while lo > 0 and key is not None and _author_key(parts[lo - 1]) == key:
        lo -= 1
    while hi < len(parts) - 1 and key is not None and _author_key(parts[hi + 1]) == key:
        hi += 1
    return parts[lo : hi + 1]


def thread_text(parts: list[XPost], *, max_chars: int = THREAD_MAX_CHARS) -> str:
    """Тред одним текстом: «1/N текст» через пустую строку; длинный — обрезан по частям."""
    if len(parts) == 1:
        return parts[0].text[:max_chars]
    n = len(parts)
    chunks: list[str] = []
    size = 0
    for i, part in enumerate(parts, 1):
        chunk = f"{i}/{n} {part.text}"
        extra = len(chunk) + (2 if chunks else 0)
        if size + extra > max_chars:
            if not chunks:
                return chunk[:max_chars]
            if size + 3 <= max_chars:
                chunks.append("…")
            break
        chunks.append(chunk)
        size += extra
    return "\n\n".join(chunks)


class _Fetcher(Protocol):
    async def fetch(self, ref: PostRef) -> XPost: ...


@runtime_checkable
class TargetReader(Protocol):
    async def fetch_target(self, ref: PostRef) -> XPost: ...


async def read_target(reader: _Fetcher, ref: PostRef) -> XPost:
    """Пост по ссылке для ответа/цитаты: у PublicPostReader — тред целиком, иначе fetch."""
    if isinstance(reader, TargetReader):
        return await reader.fetch_target(ref)
    return await reader.fetch(ref)


def _cursor(data: dict[str, Any]) -> str | None:
    cursor = data.get("cursor")
    if isinstance(cursor, dict):
        return _str(cursor.get("bottom"))
    return _str(cursor)


def _likes(post: XPost) -> int:
    return post.metrics.likes if post.metrics else 0


class PublicPostReader:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout: float = TIMEOUT,
        min_interval: float = MIN_INTERVAL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        per_minute: int = PER_MINUTE,
    ) -> None:
        self._http = http
        self._timeout = timeout
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._per_minute = per_minute
        self._sent: deque[float] = deque()  # моменты запросов за последнюю минуту
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
        data = await self._get_json(f"/thread/{ref.id}")
        return self._own_parts(ref, data)

    async def fetch_target(self, ref: PostRef) -> XPost:
        """Пост с текстом всего треда; сбой /2/thread — обычный get_post."""
        try:
            data = await self._get_json(f"/thread/{ref.id}")
            focal = parse_fxtwitter(ref, data)
            parts = self._own_parts(ref, data)
        except (XNotFound, XUnavailable) as exc:
            log.info("x.public_read.thread_failed", post_id=ref.id, error=type(exc).__name__)
            return await self.get_post(ref)
        log.info("x.public_read", via="fxtwitter_thread", post_id=ref.id, parts=len(parts))
        if len(parts) > 1:
            return replace(focal, text=thread_text(parts))
        return focal

    async def fetch_replies(self, post_id: str, limit: int = 30) -> list[XPost]:
        """Ответы под постом (/2/conversation, по лайкам), без ответов самого автора.

        Текст — без «@хэндл» в начале; ответы только из картинки (без текста) пропущены.
        Сбой первой страницы — XUnavailable/XNotFound; сбой следующих — то, что успели.
        """
        return await self._collect(
            f"/conversation/{post_id}",
            "replies",
            post_id,
            limit,
            params={"ranking_mode": "likes"},
            fill=True,
        )

    async def fetch_quotes(self, post_id: str, limit: int = 30) -> list[XPost]:
        """Цитаты поста (/2/status/{id}/quotes), самые залайканные из MAX_PAGES страниц.

        FxEmbed отдаёт цитаты от новых к старым, поэтому листаем до MAX_PAGES и сортируем.
        """
        return await self._collect(
            f"/status/{post_id}/quotes", "results", post_id, limit, params={}, fill=False
        )

    async def fetch_profile(self, handle: str) -> XProfile:
        """Публичный профиль (/2/profile/{handle}). Пока нигде не используется."""
        data = await self._get_json(f"/profile/{handle}")
        user = data.get("user")
        if not isinstance(user, dict):
            raise XUnavailable(f"FxEmbed: нет данных профиля {handle}")
        return XProfile(
            handle=_str(user.get("screen_name")) or handle,
            id=_str(user.get("id")),
            name=_str(user.get("name")),
            description=_str(user.get("description")),
            followers=_int(user.get("followers")),
            following=_int(user.get("following")),
            statuses=_int(user.get("statuses")),
        )

    @staticmethod
    def _own_parts(ref: PostRef, data: dict[str, Any]) -> list[XPost]:
        raw = data.get("thread")
        parts = [p for p in map(parse_status, raw if isinstance(raw, list) else []) if p]
        own = own_thread(ref.id, parts)
        return own or [parse_fxtwitter(ref, data)]

    async def _collect(
        self,
        path: str,
        key: str,
        post_id: str,
        limit: int,
        *,
        params: dict[str, str],
        fill: bool,
    ) -> list[XPost]:
        """Листание по cursor: до MAX_PAGES страниц; fill — стоп, как только набрали limit."""
        out: list[XPost] = []
        seen: set[str] = set()
        author: str | None = None
        cursor: str | None = None
        for page in range(MAX_PAGES):
            query = {**params, **({"cursor": cursor} if cursor else {})}
            try:
                data = await self._get_json(path, query)
            except (XNotFound, XUnavailable) as exc:
                if page == 0:
                    raise
                log.info("x.public_read.page_failed", path=path, page=page, error=str(exc))
                break
            if page == 0:
                focal = data.get("status")
                if isinstance(focal, dict) and isinstance(focal.get("author"), dict):
                    author = _str(focal["author"].get("id"))
            items = data.get(key)
            new = 0
            for obj in items if isinstance(items, list) else []:
                post = parse_status(obj, strip_mentions=True)
                if post is None or post.id in seen or post.id == post_id:
                    continue
                if author is not None and post.author_id == author:
                    continue  # части треда самого автора — не ответы
                seen.add(post.id)
                out.append(post)
                new += 1
            cursor = _cursor(data)
            if not cursor or new == 0 or (fill and len(out) >= limit):
                break
        out.sort(key=_likes, reverse=True)
        return out[:limit]

    async def _pace(self) -> None:
        """Пауза между запросами и не больше per_minute запросов в любые 60 с (ADR 0034).

        Лимит сервиса — 1000 в минуту с IP; общий ограничитель держит запас под чтение
        постов в боте, пока сбор ответов листает страницы.
        """
        async with self._lock:
            if self._last is not None:
                wait = self._min_interval - (self._clock() - self._last)
                if wait > 0:
                    await self._sleep(wait)
            now = self._clock()
            while self._sent and now - self._sent[0] >= 60.0:
                self._sent.popleft()
            if len(self._sent) >= self._per_minute:
                await self._sleep(60.0 - (now - self._sent[0]))
                now = self._clock()
                while self._sent and now - self._sent[0] >= 60.0:
                    self._sent.popleft()
            self._sent.append(now)
            self._last = now

    async def _get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        await self._pace()
        try:
            resp = await self._http.get(
                f"{FX_V2}{path}", params=params or None, timeout=self._timeout, headers=HEADERS
            )
        except httpx.HTTPError as exc:
            raise XUnavailable(f"fxtwitter недоступен: {type(exc).__name__}") from None
        if resp.status_code in (401, 403):  # Cloudflare/доступ сервиса, а не «пост удалён»
            raise XUnavailable(f"fxtwitter отказал ({resp.status_code}) для {path}")
        if resp.status_code == 404:
            raise XNotFound(f"{path}: не найден или закрыт (fxtwitter 404)")
        if resp.status_code != 200:
            raise XUnavailable(f"fxtwitter вернул {resp.status_code} для {path}")
        try:
            data = resp.json()
        except ValueError:
            raise XUnavailable(f"fxtwitter: не JSON для {path}") from None
        if not isinstance(data, dict):
            raise XUnavailable(f"fxtwitter: неожиданный ответ для {path}")
        if data.get("code") == 404:
            raise XNotFound(f"{path}: не найден или закрыт (fxtwitter 404)")
        return data

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
        try:
            data = await self._get_json(f"/status/{ref.id}")
        except XNotFound:
            raise XNotFound(f"пост {ref.id} не найден или закрыт (fxtwitter 404)") from None
        return parse_fxtwitter(ref, data)
