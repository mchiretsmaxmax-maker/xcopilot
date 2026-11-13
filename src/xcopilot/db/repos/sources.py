"""Источники и извлечённые документы (эталонный репозиторий тенанта)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
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

    # --- ингест (этап 7) ----------------------------------------------------------------

    async def get_for_update(self, source_id: UUID) -> Source | None:
        """Источник с блокировкой строки до конца транзакции (обработчик идемпотентен)."""
        stmt = (
            self.scoped()
            .where(Source.id == source_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def merge_meta(self, source_id: UUID, **patch: Any) -> Source:
        """Дописать ключи в meta источника."""
        src = await self.require(source_id)
        return await self.update(source_id, meta={**src.meta, **patch})

    async def documents(self, source_id: UUID) -> list[Document]:
        """Документы своего источника в порядке создания."""
        stmt = (
            select(Document)
            .where(Document.account_id == self.account_id, Document.source_id == source_id)
            .order_by(Document.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_document(self, document_id: UUID, **values: Any) -> None:
        """Изменить текстовые поля своего документа (text, text_clean, token_count)."""
        await self.session.execute(
            update(Document)
            .where(Document.id == document_id, Document.account_id == self.account_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    async def lock_batch(self, batch_id: str) -> list[Source]:
        """Источники одной партии приёма (meta.batch) с блокировкой строк, по порядку id."""
        stmt = (
            self.scoped()
            .where(Source.meta["batch"].astext == batch_id)
            .order_by(Source.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def set_kind_for_batch(self, batch_id: str, kind: str) -> int:
        """Сменить вид у обработанных источников партии; вернуть число изменённых."""
        stmt = (
            update(Source)
            .where(
                Source.account_id == self.account_id,
                Source.meta["batch"].astext == batch_id,
                Source.status == "indexed",
            )
            .values(kind=kind)
            .returning(Source.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self.session.execute(stmt)).all())

    async def count_documents(self, *, kind: str, status: str) -> int:
        """Число документов у источников аккаунта данного вида и статуса."""
        stmt = (
            select(func.count(Document.id))
            .join(Source, Source.id == Document.source_id)
            .where(
                Source.account_id == self.account_id,
                Source.kind == kind,
                Source.status == status,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())
