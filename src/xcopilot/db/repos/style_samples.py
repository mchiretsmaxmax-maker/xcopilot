"""Куски собственных постов аккаунта — материал для паспорта стиля."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, and_, select

from xcopilot.db.models import Chunk, Document, Source
from xcopilot.db.repos.base import TenantRepo


@dataclass(frozen=True)
class OwnChunk:
    id: UUID
    text: str
    tags: list[str]


def own_voice() -> ColumnElement[bool]:
    """Предикат «свой голос»: материал владельца, готовый к поиску.

    Единственное место фильтра: ретрофит БД (ТЗ v3.1) переключит его на `sources.stance = 'own'`.
    """
    return and_(Source.kind == "own_post", Source.status == "indexed")


class StyleSamplesRepo(TenantRepo[Chunk]):
    model = Chunk

    async def own_post_chunks(self, *, limit: int = 500) -> list[OwnChunk]:
        """Куски своего голоса (own_voice), новые первыми, не более limit."""
        stmt = (
            select(Chunk.id, Chunk.text, Chunk.tags)
            .join(Document, Document.id == Chunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(
                Chunk.account_id == self.account_id,
                own_voice(),
            )
            .order_by(Chunk.id.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [OwnChunk(id=r[0], text=r[1], tags=list(r[2])) for r in rows]
