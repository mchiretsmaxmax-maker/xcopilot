"""Карточки черновиков и выборки этапа 12 (решения, публикация, автосверка, «Почему так»)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import (
    Draft,
    KnowledgeApplication,
    KnowledgeItem,
    Publication,
    Review,
    ReviewCard,
)
from xcopilot.db.repos.base import TenantRepo

# черновики, ждущие подтверждения публикации
AWAITING = ("approved", "edited")


@dataclass(frozen=True)
class AwaitingDraft:
    draft_id: UUID
    decided_at: datetime
    text: str


@dataclass(frozen=True)
class AppliedRow:
    guidance: str
    slot: str
    applied: bool | None


class ReviewCardsRepo(TenantRepo[ReviewCard]):
    model = ReviewCard

    async def for_chat(self, run_id: UUID, chat_id: int) -> ReviewCard | None:
        stmt = self.scoped().where(self.col("run_id") == run_id, self.col("chat_id") == chat_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def by_run(self, run_id: UUID) -> list[ReviewCard]:
        stmt = self.scoped().where(self.col("run_id") == run_id).order_by(self.col("id"))
        return list((await self.session.execute(stmt)).scalars().all())

    async def shown_runs_since(self, since: datetime) -> int:
        """Сколько разных запусков показано с момента since (для дневного предела)."""
        stmt = select(func.count(func.distinct(ReviewCard.run_id))).where(
            ReviewCard.account_id == self.account_id,
            ReviewCard.shown_at.is_not(None),
            ReviewCard.shown_at >= since,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def stale(self, shown_before: datetime, queued_before: datetime) -> list[ReviewCard]:
        """Показанные раньше shown_before и ждущие в очереди дольше queued_before."""
        stmt = self.scoped().where(
            ((self.col("state") == "shown") & (self.col("shown_at") < shown_before))
            | ((self.col("state") == "queued") & (self.col("created_at") < queued_before))
        )
        return list((await self.session.execute(stmt.order_by(self.col("id")))).scalars().all())

    async def queued(self) -> list[ReviewCard]:
        stmt = self.scoped().where(self.col("state") == "queued").order_by(self.col("id"))
        return list((await self.session.execute(stmt)).scalars().all())

    async def detached(self) -> list[ReviewCard]:
        """Показанные карточки, чьи кнопки сняты новым действием (ждут решения в /queue)."""
        stmt = self.scoped().where(self.col("state") == "shown", self.col("message_id").is_(None))
        return list((await self.session.execute(stmt.order_by(self.col("id")))).scalars().all())


async def detach_card_messages(session: AsyncSession, chat_id: int, message_ids: list[int]) -> None:
    """Кнопки карточек сняты (новое действие): сообщения больше не «живые», карточка — в /queue.

    Только показанные карточки; message_id = NULL значит «показать заново из очереди».
    """
    await session.execute(
        update(ReviewCard)
        .where(
            ReviewCard.chat_id == chat_id,
            ReviewCard.message_id.in_(message_ids),
            ReviewCard.state == "shown",
        )
        .values(message_id=None)
    )


class ReviewQueries:
    """Выборки этапа 12 по нескольким таблицам; всё — в пределах аккаунта."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def lock_draft(self, draft_id: UUID) -> Draft | None:
        """Черновик аккаунта с блокировкой строки до конца транзакции (идемпотентность нажатий)."""
        stmt = (
            select(Draft)
            .where(Draft.id == draft_id, Draft.account_id == self.account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        draft: Draft | None = (await self.session.execute(stmt)).scalar_one_or_none()
        return draft

    async def last_edit_text(self, draft_id: UUID) -> str | None:
        stmt = (
            select(Review.text_after)
            .where(
                Review.account_id == self.account_id,
                Review.draft_id == draft_id,
                Review.action == "edit",
            )
            .order_by(Review.created_at.desc(), Review.id.desc())
            .limit(1)
        )
        text: str | None = (await self.session.execute(stmt)).scalar_one_or_none()
        return text

    async def applied_rows(self, draft_id: UUID) -> list[AppliedRow]:
        stmt = (
            select(KnowledgeItem.guidance, KnowledgeApplication.slot, KnowledgeApplication.applied)
            .join(KnowledgeItem, KnowledgeItem.id == KnowledgeApplication.item_id)
            .where(
                KnowledgeApplication.account_id == self.account_id,
                KnowledgeApplication.draft_id == draft_id,
            )
            .order_by(KnowledgeApplication.id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [AppliedRow(r[0], r[1], r[2]) for r in rows]

    async def awaiting_publication(self) -> list[AwaitingDraft]:
        """Одобренные/исправленные без публикации: время решения и итоговый текст."""
        decided = (
            select(
                Review.draft_id,
                func.max(Review.created_at).label("decided_at"),
            )
            .where(Review.account_id == self.account_id, Review.action.in_(("approve", "edit")))
            .group_by(Review.draft_id)
            .subquery()
        )
        stmt = (
            select(Draft.id, decided.c.decided_at, Draft.text)
            .join(decided, decided.c.draft_id == Draft.id)
            .where(Draft.account_id == self.account_id, Draft.status.in_(AWAITING))
            .order_by(decided.c.decided_at, Draft.id)
        )
        out: list[AwaitingDraft] = []
        for draft_id, decided_at, text in (await self.session.execute(stmt)).all():
            edited = await self.last_edit_text(draft_id)
            out.append(AwaitingDraft(draft_id, decided_at, edited or text))
        return out

    async def published_post_ids(self) -> set[str]:
        stmt = select(Publication.x_post_id).where(Publication.account_id == self.account_id)
        return set((await self.session.execute(stmt)).scalars().all())
