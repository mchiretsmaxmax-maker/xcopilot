"""Куски собственных постов аккаунта — материал для паспорта стиля."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, and_, select

from xcopilot.db.models import Chunk, Document, Source
from xcopilot.db.repos.base import TenantRepo
from xcopilot.db.repos.sources import own_voice as stance_own


@dataclass(frozen=True)
class OwnChunk:
    id: UUID
    text: str
    tags: list[str]


def own_voice() -> ColumnElement[bool]:
    """Предикат «свой голос» для паспорта: свой материал (ТЗ v3.1, stance = own — см.
    `sources.own_voice`), проиндексирован и не убран владельцем."""
    return and_(stance_own(), Source.status == "indexed", Source.removed_at.is_(None))


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
