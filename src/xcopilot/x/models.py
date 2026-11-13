"""Модели X: ссылка на пост, пост, пользователь, метрики."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

PostSource = Literal["oembed", "api"]


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
    possibly_truncated: bool = False
    conversation_id: str | None = None
    in_reply_to_id: str | None = None
