"""Куски собственных постов аккаунта — материал для паспорта стиля."""

from __future__ import annotations

from collections.abc import Collection
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
    source_id: UUID | None = None


@dataclass(frozen=True)
class OwnSourceText:
    """Свой источник целиком (все документы): для фильтра AI-шаблонов «Чистого голоса»."""

    source_id: UUID
    text: str
    author_handle: str | None
    url: str | None


def own_voice() -> ColumnElement[bool]:
    """Предикат «свой голос» для паспорта: свой материал (ТЗ v3.1, stance = own — см.
    `sources.own_voice`), проиндексирован и не убран владельцем."""
    return and_(stance_own(), Source.status == "indexed", Source.removed_at.is_(None))


class StyleSamplesRepo(TenantRepo[Chunk]):
    model = Chunk

    async def own_post_chunks(
        self, *, limit: int = 500, exclude_sources: Collection[UUID] = ()
    ) -> list[OwnChunk]:
        """Куски своего голоса (own_voice), новые первыми, не более limit; без exclude_sources."""
        stmt = (
            select(Chunk.id, Chunk.text, Chunk.tags, Source.id)
            .join(Document, Document.id == Chunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(
                Chunk.account_id == self.account_id,
                own_voice(),
            )
            .order_by(Chunk.id.desc())
            .limit(limit)
        )
        if exclude_sources:
            stmt = stmt.where(Source.id.notin_(list(exclude_sources)))
        rows = (await self.session.execute(stmt)).all()
        return [OwnChunk(id=r[0], text=r[1], tags=list(r[2]), source_id=r[3]) for r in rows]

    async def own_source_texts(self) -> list[OwnSourceText]:
        """Свои источники (own_voice) с полным текстом документов, по id."""
        stmt = (
            select(Source.id, Source.author_handle, Source.url, Document.text_clean, Document.text)
            .join(Document, Document.source_id == Source.id)
            .where(Source.account_id == self.account_id, own_voice())
            .order_by(Source.id, Document.id)
        )
        merged: dict[UUID, OwnSourceText] = {}
        for sid, handle, url, clean, raw in (await self.session.execute(stmt)).all():
            body = clean if clean is not None else raw
            prev = merged.get(sid)
            joined = body if prev is None else f"{prev.text}\n\n{body}"
            merged[sid] = OwnSourceText(sid, joined, handle, url)
        return list(merged.values())
