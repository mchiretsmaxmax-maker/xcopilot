"""Запросы ворот качества (этап 11): свои посты, кандидаты чужого материала, знания класса C,
ревизии черновика и перенос `knowledge_applications` на финальную ревизию."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Account
from xcopilot.db.models.brain import KnowledgeApplication

FOREIGN_TRGM = 0.3  # порог pg_trgm для отбора кандидатов; точная проверка — в правиле линтера


class QualityRepo:
    """Чтения и записи ворот; всё ограничено своим account_id."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def account_settings(self) -> dict[str, Any]:
        acc = await self.session.get(Account, self.account_id)
        return dict(acc.settings or {}) if acc is not None else {}

    async def own_recent(self, since: datetime, *, limit: int = 300) -> list[str]:
        """Тексты своих материалов (stance own, не убраны) с даты since."""
        rows = await self.session.execute(
            text(
                "SELECT coalesce(d.text_clean, d.text) FROM documents d"
                " JOIN sources s ON s.id = d.source_id"
                " WHERE d.account_id = :a AND s.stance = 'own' AND s.removed_at IS NULL"
                " AND coalesce(s.posted_at, s.created_at) >= :since"
                " ORDER BY coalesce(s.posted_at, s.created_at) DESC LIMIT :n"
            ),
            {"a": self.account_id, "since": since, "n": limit},
        )
        return [r[0] for r in rows.all() if r[0]]

    async def foreign_candidates(self, draft: str, *, limit: int = 30) -> list[tuple[UUID, str]]:
        """Чужие материалы (stance ≠ own), похожие на черновик по триграммам в любую сторону:
        целиком, черновик внутри материала или материал внутри черновика."""
        await self.session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {FOREIGN_TRGM}"))
        await self.session.execute(
            text(f"SET LOCAL pg_trgm.word_similarity_threshold = {FOREIGN_TRGM}")
        )
        rows = await self.session.execute(
            text(
                "SELECT s.id, coalesce(d.text_clean, d.text) AS body FROM documents d"
                " JOIN sources s ON s.id = d.source_id"
                " WHERE d.account_id = :a AND s.stance <> 'own' AND s.removed_at IS NULL"
                " AND (coalesce(d.text_clean, d.text) % :q"
                "      OR :q <% coalesce(d.text_clean, d.text)"
                "      OR coalesce(d.text_clean, d.text) <% :q)"
                " ORDER BY greatest(similarity(coalesce(d.text_clean, d.text), :q),"
                "   word_similarity(:q, coalesce(d.text_clean, d.text)),"
                "   word_similarity(coalesce(d.text_clean, d.text), :q)) DESC"
                " LIMIT :n"
            ),
            {"a": self.account_id, "q": draft, "n": limit},
        )
        return [(r[0], r[1]) for r in rows.all() if r[1]]

    async def class_c_items(self, global_id: UUID) -> list[tuple[UUID, str]]:
        """Рабочие знания класса C аккаунта и общего аккаунта: (id, утверждение)."""
        rows = await self.session.execute(
            text(
                "SELECT id, statement FROM knowledge_items"
                " WHERE account_id IN (:a, :g) AND risk_class = 'C' AND status = 'active'"
                " ORDER BY account_id = :a DESC, quality DESC NULLS LAST, id"
            ),
            {"a": self.account_id, "g": global_id},
        )
        return [(r[0], r[1]) for r in rows.all()]

    async def child_revision(self, draft_id: UUID) -> UUID | None:
        """Следующая ревизия черновика (если ворота уже переписывали его)."""
        rows = await self.session.execute(
            text(
                "SELECT id FROM drafts WHERE account_id = :a AND parent_draft_id = :d"
                " ORDER BY revision DESC LIMIT 1"
            ),
            {"a": self.account_id, "d": draft_id},
        )
        return rows.scalar_one_or_none()

    async def root_revision(self, draft_id: UUID) -> UUID:
        """Первая ревизия варианта (по цепочке parent_draft_id)."""
        rows = await self.session.execute(
            text(
                "WITH RECURSIVE up AS ("
                "  SELECT id, parent_draft_id FROM drafts WHERE account_id = :a AND id = :d"
                "  UNION ALL"
                "  SELECT p.id, p.parent_draft_id FROM drafts p"
                "  JOIN up ON p.id = up.parent_draft_id WHERE p.account_id = :a"
                ") SELECT id FROM up WHERE parent_draft_id IS NULL"
            ),
            {"a": self.account_id, "d": draft_id},
        )
        return rows.scalar_one_or_none() or draft_id

    async def applications(self, draft_id: UUID) -> list[KnowledgeApplication]:
        stmt = (
            select(KnowledgeApplication)
            .where(
                KnowledgeApplication.account_id == self.account_id,
                KnowledgeApplication.draft_id == draft_id,
            )
            .order_by(KnowledgeApplication.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())
