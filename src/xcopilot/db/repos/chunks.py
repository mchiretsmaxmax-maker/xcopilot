"""Куски документов и их векторы."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from xcopilot.db.models import Chunk, Embedding
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
