"""Блок публикации (ТЗ, этап 12, задачи 4, 9, 10): Web Intent или копирование, подтверждение.

Бот ничего не пишет в X: публикует владелец по ссылке Intent (этап 6) или копирует текст.
Длиннее 280 взвешенных знаков и треды — режим копирования (тред — блок на каждую часть).
Ответ — Intent с `in_reply_to`, цитата — с адресом цитируемого поста в конце текста.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import structlog

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, DraftsRepo, PublicationsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.review import ReviewQueries
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.metrics.events import EventType, emit
from xcopilot.x.client import XClient
from xcopilot.x.errors import XError
from xcopilot.x.intents import build_post_intent, build_quote_intent, build_reply_intent
from xcopilot.x.models import PostRef, XPost
from xcopilot.x.urls import parse_post_url

log = structlog.get_logger()


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


def split_parts(text: str) -> list[str]:
    """Части треда из текста, присланного владельцем: по пустым строкам."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def publish_view(draft: PublishDraft) -> PublishView:
    """Что показать после одобрения: ссылка Intent или блоки для копирования."""
    if draft.format == "thread":
        blocks = list(draft.parts) or split_parts(draft.text)
        return PublishView(draft.draft_id, "copy", draft.text, None, blocks)
    intent: str | None
    target = parse_post_url(draft.reply_to_url) if draft.reply_to_url else None
    if draft.format == "reply" and target is not None:
        intent = build_reply_intent(draft.text, target.id)
    elif draft.format == "quote" and draft.reply_to_url:
        intent = build_quote_intent(draft.text, draft.reply_to_url)
    else:
        intent = build_post_intent(draft.text)
    mode: Literal["intent", "copy"] = "intent" if intent is not None else "copy"
    return PublishView(draft.draft_id, mode, draft.text, intent, [draft.text])


async def record_publication(
    session: DbSession,
    account_id: UUID,
    draft_id: UUID,
    *,
    post: PostRef,
    final_text: str,
    published_at: datetime,
    matched_by: str,
    score: float | None,
) -> Confirmed:
    """Публикация черновика: строка publications, статус published, события; идемпотентно."""
    pubs = PublicationsRepo(session, account_id)
    if await ReviewQueries(session, account_id).lock_draft(draft_id) is None:
        raise NotFound(f"черновик {draft_id} не найден")
    existing = await pubs.get_by_draft(draft_id)
    if existing is not None:
        return _confirmed(existing, repeated=True)
    taken = await pubs.get_by_x_post_id(post.id)
    if taken is not None:
        raise LinkError("этот пост уже связан с другим черновиком")
    await DraftsRepo(session, account_id).set_status(draft_id, DraftStatus.PUBLISHED)
    row = await pubs.add(
        draft_id=draft_id,
        x_post_id=post.id,
        url=post.url,
        published_at=published_at,
        matched_by=matched_by,
        match_score=score,
        final_text=final_text,
    )
    entity = ("draft", draft_id)
    if matched_by != "user_link" and score is not None:
        await emit(
            session,
            account_id=account_id,
            type=EventType.PUBLICATION_MATCHED,
            entity=entity,
            props={"x_post_id": post.id, "score": round(score, 4)},
        )
    await emit(
        session,
        account_id=account_id,
        type=EventType.PUBLICATION_CONFIRMED,
        entity=entity,
        props={"x_post_id": post.id, "matched_by": matched_by},
    )
    return _confirmed(row)


def _confirmed(row: Any, *, repeated: bool = False) -> Confirmed:
    return Confirmed(
        publication_id=row.id,
        draft_id=row.draft_id,
        x_post_id=row.x_post_id,
        url=row.url,
        final_text=row.final_text,
        matched_by=row.matched_by,
        published_at=row.published_at,
        repeated=repeated,
    )


async def draft_for_publish(session: DbSession, account_id: UUID, draft_id: UUID) -> PublishDraft:
    """Черновик с итоговым текстом: последняя правка владельца или текст ворот."""
    row = await DraftsRepo(session, account_id).require(draft_id)
    edited = await ReviewQueries(session, account_id).last_edit_text(draft_id)
    if edited is not None:
        parts = split_parts(edited) if row.format == "thread" else []
        return PublishDraft(row.id, row.format, edited, parts, row.reply_to_url)
    return PublishDraft(row.id, row.format, row.text, list(row.parts or []), row.reply_to_url)


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
        async with self._uow() as uow:
            return publish_view(await draft_for_publish(uow.session, account_id, draft_id))

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
        if url is None:
            if self._on_find is not None:
                await self._on_find(account_id, draft_id)
            return None
        ref = parse_post_url(url)
        if ref is None:
            raise LinkError("нет ссылки на пост X")
        async with self._uow() as uow:
            account = await AccountsRepo(uow.session).get(account_id)
            if account is None:
                raise NotFound(f"аккаунт {account_id} не найден")
            handle = account.handle
            draft = await draft_for_publish(uow.session, account_id, draft_id)
        if ref.handle is not None and ref.handle.lower() != handle.lower():
            raise ForeignLinkError(f"ссылка на @{ref.handle}, а аккаунт @{handle}")
        post = await self._read_post(account_id, ref)
        if post is not None and post.author_handle and post.author_handle.lower() != handle.lower():
            raise ForeignLinkError(f"пост @{post.author_handle}, а аккаунт @{handle}")
        own = PostRef(ref.id, handle)
        async with self._uow() as uow:
            return await record_publication(
                uow.session,
                account_id,
                draft_id,
                post=own,
                final_text=post.text if post is not None else draft.text,
                published_at=(post.created_at if post and post.created_at else self._clock.now()),
                matched_by=matched_by,
                score=score,
            )

    async def _read_post(self, account_id: UUID, ref: PostRef) -> XPost | None:
        """Пост из X (только чтение); X недоступен или выключен — None."""
        if self._x_for is None:
            return None
        try:
            client = await self._x_for(account_id)
            return await client.get_post(ref)
        except XError as exc:
            log.info("publish_post_unreadable", post_id=ref.id, error=type(exc).__name__)
            return None
