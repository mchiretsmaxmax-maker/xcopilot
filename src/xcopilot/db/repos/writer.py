"""Запросы писателя (этап 10): свои посты, теги свежих материалов, повторы тем, поиск поста."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Chunk, Document, Source, Topic


class WriterRepo:
    """Чтения для писателя; всё ограничено своим account_id."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def own_posts(self, limit: int) -> list[str]:
        """Тексты своих последних постов (stance own, проиндексированы, не убраны)."""
        stmt = (
            select(func.coalesce(Document.text_clean, Document.text))
            .join(Source, Source.id == Document.source_id)
            .where(
                Document.account_id == self.account_id,
                Source.stance == "own",
                Source.removed_at.is_(None),
                Source.status == "indexed",
            )
            .order_by(func.coalesce(Source.posted_at, Source.created_at).desc(), Source.id.desc())
            .limit(limit)
        )
        return [t for t in (await self.session.execute(stmt)).scalars() if t]

    async def fresh_tags(self, since: datetime, *, prefix: str, limit: int) -> list[str]:
        """Самые частые теги кусков с префиксом с момента since (без префикса)."""
        tag = func.unnest(Chunk.tags).label("tag")
        inner = (
            select(tag)
            .where(Chunk.account_id == self.account_id, Chunk.created_at >= since)
            .subquery()
        )
        stmt = (
            select(inner.c.tag, func.count())
            .where(inner.c.tag.like(f"{prefix}%"))
            .group_by(inner.c.tag)
            .order_by(func.count().desc(), inner.c.tag)
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [str(t).removeprefix(prefix) for t, _ in rows]

    async def topic_vectors_since(self, since: datetime) -> list[list[float]]:
        """Векторы тем аккаунта, созданных с момента since (для дедупа)."""
        stmt = select(Topic.dedupe_vec).where(
            Topic.account_id == self.account_id,
            Topic.created_at >= since,
            Topic.dedupe_vec.is_not(None),
        )
        return [[float(x) for x in v] for v in (await self.session.execute(stmt)).scalars()]

    async def source_id_by_url(self, url: str) -> UUID | None:
        """Не убранный источник с документом по ссылке (последний)."""
        stmt = (
            select(Source.id)
            .where(Source.account_id == self.account_id, Source.url == url)
            .where(Source.removed_at.is_(None))
            .where(select(Document.id).where(Document.source_id == Source.id).exists())
            .order_by(Source.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def taxonomy_version(self) -> int:
        """Текущая версия таксономии (наибольшая since_version, по умолчанию 1)."""
        row = await self.session.execute(
            text("SELECT coalesce(max(since_version), 1) FROM taxonomy_terms")
        )
        return int(row.scalar_one())
