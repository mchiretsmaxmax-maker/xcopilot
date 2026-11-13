"""Куски документов и их векторы."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Chunk, Document, Embedding, EmbeddingModel
from xcopilot.db.repos.base import TenantRepo


class ChunksRepo(TenantRepo[Chunk]):
    model = Chunk

    async def list_by_document(self, document_id: UUID) -> list[Chunk]:
        """Куски своего аккаунта этого документа по возрастанию idx."""
        stmt = self.scoped().where(Chunk.document_id == document_id).order_by(Chunk.idx)
        return list((await self.session.execute(stmt)).scalars().all())

    async def add_embedding(
        self, chunk_id: UUID, *, model_id: UUID, vec: Sequence[float]
    ) -> Embedding:
        """Добавить вектор куска; чужой кусок — NotFound."""
        await self.require(chunk_id)
        emb = Embedding(
            account_id=self.account_id,
            chunk_id=chunk_id,
            model_id=model_id,
            vec=list(vec),
        )
        self.session.add(emb)
        await self.session.flush()
        await self.session.refresh(emb)
        return emb

    # --- ингест (этап 7) ----------------------------------------------------------------

    async def add_chunk(
        self, document_id: UUID, *, idx: int, text: str, tags: Sequence[str], lang: str | None
    ) -> Chunk:
        """Добавить кусок документа своего аккаунта."""
        return await self.add(
            document_id=document_id, idx=idx, text=text, tags=list(tags), lang=lang
        )

    async def count_for_source(self, source_id: UUID) -> int:
        stmt = (
            select(func.count(Chunk.id))
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.account_id == self.account_id, Document.source_id == source_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def find_similar(
        self, text: str, *, threshold: float, exclude_source_id: UUID | None = None
    ) -> tuple[UUID, UUID] | None:
        """Самый похожий кусок аккаунта (pg_trgm similarity > threshold): (chunk_id, source_id)."""
        sim = func.similarity(Chunk.text, text)
        stmt = (
            select(Chunk.id, Document.source_id)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.account_id == self.account_id,
                Chunk.text.op("%")(text),
                sim > threshold,
            )
            .order_by(sim.desc(), Chunk.id)
            .limit(1)
        )
        if exclude_source_id is not None:
            stmt = stmt.where(Document.source_id != exclude_source_id)
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1]) if row is not None else None

    async def source_of(self, chunk_id: UUID) -> UUID | None:
        """Источник своего куска (через документ) или None."""
        stmt = (
            select(Document.source_id)
            .join(Chunk, Chunk.document_id == Document.id)
            .where(Chunk.id == chunk_id, Chunk.account_id == self.account_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def tags_for_sources(self, source_ids: Sequence[UUID]) -> list[list[str]]:
        """Метки всех кусков указанных источников."""
        if not source_ids:
            return []
        stmt = (
            select(Chunk.tags)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.account_id == self.account_id, Document.source_id.in_(source_ids))
            .order_by(Chunk.id)
        )
        return [list(t) for t in (await self.session.execute(stmt)).scalars().all()]


class EmbeddingModelsRepo:
    """Глобальный реестр моделей эмбеддингов."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active(self) -> EmbeddingModel | None:
        stmt = select(EmbeddingModel).where(EmbeddingModel.active.is_(True)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()
