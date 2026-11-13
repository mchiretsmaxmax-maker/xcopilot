"""Источники и извлечённые документы (эталонный репозиторий тенанта)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert

from xcopilot.db.models import Document, Source
from xcopilot.db.repos.base import TenantRepo


class SourcesRepo(TenantRepo[Source]):
    """Источники аккаунта; дедуп по content_hash."""

    model = Source

    async def add_or_get(
        self, *, kind: str, origin: str, content_hash: str, **fields: Any
    ) -> tuple[Source, bool]:
        """Вставить источник; если такой content_hash уже есть — вернуть существующий.

        Возвращает (источник, создан_ли_сейчас).
        """
        stmt = (
            insert(Source)
            .values(
                account_id=self.account_id,
                kind=kind,
                origin=origin,
                content_hash=content_hash,
                **fields,
            )
            .on_conflict_do_nothing(index_elements=["account_id", "content_hash"])
            .returning(Source.id)
        )
        new_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if new_id is not None:
            return await self.require(new_id), True
        existing = await self.session.execute(
            self.scoped().where(Source.content_hash == content_hash)
        )
        return existing.scalar_one(), False

    async def set_status(self, source_id: UUID, status: str) -> Source:
        return await self.update(source_id, status=status)

    async def add_document(
        self,
        source_id: UUID,
        *,
        text: str,
        text_clean: str | None = None,
        token_count: int | None = None,
    ) -> Document:
        """Добавить текст к своему источнику; чужой источник — NotFound."""
        await self.require(source_id)
        doc = Document(
            account_id=self.account_id,
            source_id=source_id,
            text=text,
            text_clean=text_clean,
            token_count=token_count,
        )
        self.session.add(doc)
        await self.session.flush()
        await self.session.refresh(doc)
        return doc
