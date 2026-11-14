"""Куски собственных постов аккаунта — материал для паспорта стиля."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from xcopilot.db.models import Chunk, Document, Source
from xcopilot.db.repos.base import TenantRepo


@dataclass(frozen=True)
class OwnChunk:
    id: UUID
    text: str
    tags: list[str]


class StyleSamplesRepo(TenantRepo[Chunk]):
    model = Chunk

    async def own_post_chunks(self, *, limit: int = 500) -> list[OwnChunk]:
        """Куски проиндексированных источников kind=own_post, новые первыми, не более limit."""
        stmt = (
            select(Chunk.id, Chunk.text, Chunk.tags)
            .join(Document, Document.id == Chunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(
                Chunk.account_id == self.account_id,
                Source.kind == "own_post",
                Source.status == "indexed",
            )
            .order_by(Chunk.id.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [OwnChunk(id=r[0], text=r[1], tags=list(r[2])) for r in rows]
