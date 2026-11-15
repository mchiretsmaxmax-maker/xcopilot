"""Блок публикации (ТЗ, этап 12, задачи 4, 9, 10): Web Intent или копирование, подтверждение.

Бот ничего не пишет в X: публикует владелец по ссылке Intent (этап 6) или копирует текст.
Длиннее 280 взвешенных знаков и треды — режим копирования (тред — блок на каждую часть).
Ответ — Intent с `in_reply_to`, цитата — с адресом цитируемого поста в конце текста.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.x.client import XClient


class LinkError(ValueError):
    """Ссылка не на пост X."""


class ForeignLinkError(LinkError):
    """Ссылка на пост чужого аккаунта."""


@dataclass(frozen=True)
class PublishDraft:
    draft_id: UUID
    format: str
    text: str
    parts: list[str] = field(default_factory=list)
    reply_to_url: str | None = None


@dataclass(frozen=True)
class PublishView:
    draft_id: UUID
    mode: Literal["intent", "copy"]
    text: str
    intent_url: str | None
    copy_blocks: list[str]


@dataclass(frozen=True)
class Confirmed:
    publication_id: UUID
    draft_id: UUID
    x_post_id: str
    url: str
    final_text: str
    matched_by: str
    published_at: datetime
    repeated: bool = False


def publish_view(draft: PublishDraft) -> PublishView:
    """Что показать после одобрения: ссылка Intent или блоки для копирования."""
    raise NotImplementedError


class Publisher:
    """Блок публикации по черновику из БД и подтверждение публикации владельцем."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        x_for: Callable[[UUID], Awaitable[XClient]] | None = None,
        on_find: Callable[[UUID, UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._x_for = x_for
        self._on_find = on_find

    async def view(self, account_id: UUID, draft_id: UUID) -> PublishView:
        """Блок публикации: текст — последняя правка владельца или текст черновика."""
        raise NotImplementedError

    async def confirm_published(
        self,
        account_id: UUID,
        draft_id: UUID,
        url: str | None = None,
        *,
        matched_by: str = "user_link",
        score: float | None = None,
    ) -> Confirmed | None:
        """Ссылка от владельца → публикация; без ссылки — поставить автосверку, вернуть None.

        Ссылка на чужой аккаунт — ForeignLinkError, не на пост — LinkError. Итоговый текст
        берётся из самого поста X (владелец мог поправить его перед публикацией).
        """
        raise NotImplementedError
