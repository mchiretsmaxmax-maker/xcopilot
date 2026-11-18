"""Модели X: ссылка на пост, пост, пользователь, метрики."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

PostSource = Literal["oembed", "fxtwitter", "api"]


@dataclass(frozen=True, slots=True)
class PostRef:
    """Ссылка на пост: id и (если был в адресе) хэндл автора без @."""

    id: str
    handle: str | None = None

    @property
    def url(self) -> str:
        """Канонический адрес поста на x.com."""
        if self.handle is None:
            return f"https://x.com/i/web/status/{self.id}"
        return f"https://x.com/{self.handle}/status/{self.id}"


@dataclass(frozen=True, slots=True)
class XMetrics:
    """Метрики поста. Публичные есть всегда; непубличные — только у своих постов до 30 дней."""

    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0
    bookmarks: int = 0
    impressions: int | None = None
    url_clicks: int | None = None
    profile_clicks: int | None = None


@dataclass(frozen=True, slots=True)
class XUser:
    id: str
    handle: str
    name: str | None = None
    followers: int | None = None


@dataclass(frozen=True, slots=True)
class XPost:
    id: str
    text: str
    author_handle: str | None
    created_at: datetime | None
    source: PostSource
    author_name: str | None = None
    author_id: str | None = None
    metrics: XMetrics | None = None
    author_followers: int | None = None  # подписчики автора, если источник их дал
    possibly_truncated: bool = False
    conversation_id: str | None = None
    in_reply_to_id: str | None = None
    lang: str | None = None  # язык поста по X (FxEmbed v2), None — не определён
    replying_to: str | None = None  # хэндл автора родительского поста, если это ответ
    media: tuple[XMedia, ...] = ()
    is_note_tweet: bool = False  # длинный пост (больше 280 знаков)


@dataclass(frozen=True, slots=True)
class XMedia:
    """Вложение поста кратко: тип (photo, video, gif) и адрес."""

    type: str
    url: str


@dataclass(frozen=True, slots=True)
class XProfile:
    """Публичный профиль из FxEmbed v2 (пока только в клиенте)."""

    handle: str
    id: str | None = None
    name: str | None = None
    description: str | None = None
    followers: int | None = None
    following: int | None = None
    statuses: int | None = None
