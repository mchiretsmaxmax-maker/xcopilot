"""Источники и извлечённые документы (эталонный репозиторий тенанта).

ТЗ v3.1: материал описывают два поля — `kind` (что это) и `stance` (чьё и зачем).
Предикат «свой голос» — только `own_voice()` отсюда: этапы 8 и 9 зовут его, а не
сравнивают stance сами.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, case, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from xcopilot.db.models import Document, KnowledgeEvidence, Source
from xcopilot.db.repos.base import TenantRepo

OWN_STANCE = "own"

# Словарь «материала» в боте и теггере (кнопки, callback, промпт ingest.tag) → (kind, stance).
MATERIALS: dict[str, tuple[str, str]] = {
    "own_post": ("post", "own"),
    "liked_sample": ("post", "inspiration"),
    "competitor_post": ("post", "competitor"),
    "note": ("note", "reference"),
}


def material_kind_stance(material: str) -> tuple[str, str]:
    """(kind, stance) для материала из словаря бота; неизвестный — ValueError."""
    try:
        return MATERIALS[material]
    except KeyError:
        raise ValueError(f"неизвестный материал: {material!r}") from None


def source_material(kind: str, stance: str) -> str:
    """Обратное отображение (kind, stance) → словарь бота; file/chat остаются как есть."""
    if kind in ("file", "chat"):
        return kind
    if kind in ("note", "article") or stance == "reference":
        return "note"
    return {"own": "own_post", "competitor": "competitor_post"}.get(stance, "liked_sample")


def material_expr() -> ColumnElement[str]:
    """SQL-выражение «материал бота» из (kind, stance) — то же, что source_material()."""
    return case(
        (Source.kind.in_(("file", "chat")), Source.kind),
        (or_(Source.kind.in_(("note", "article")), Source.stance == "reference"), literal("note")),
        (Source.stance == OWN_STANCE, literal("own_post")),
        (Source.stance == "competitor", literal("competitor_post")),
        else_=literal("liked_sample"),
    )


def usable() -> ColumnElement[bool]:
    """Материал можно показывать моделям: не убран владельцем и не в карантине (ТЗ v3.1)."""
    return and_(Source.removed_at.is_(None), Source.analysis_status != "quarantined")


def own_voice() -> ColumnElement[bool]:
    """Условие «материал — мой голос» (единственное место предиката): stance = own,
    не убран и не в карантине."""
    return and_(Source.stance == OWN_STANCE, usable())


def ready_for_analysis() -> ColumnElement[bool]:
    """Материал ждёт разбора (этап 7A): pending, не убран и прошёл проверку на входе.

    `meta.screen = 'unavailable'` — судья был недоступен при приёме: до повторной проверки
    такой материал не разбирается.
    """
    return and_(
        Source.analysis_status == "pending",
        Source.removed_at.is_(None),
        func.coalesce(Source.meta["screen"].astext, "") != "unavailable",
    )


class SourcesRepo(TenantRepo[Source]):
    """Источники аккаунта; дедуп по content_hash."""

    model = Source

    async def add_or_get(
        self, *, kind: str, stance: str, origin: str, content_hash: str, **fields: Any
    ) -> tuple[Source, bool]:
        """Вставить источник; если такой content_hash уже есть — вернуть существующий.

        Возвращает (источник, создан_ли_сейчас).
        """
        stmt = (
            insert(Source)
            .values(
                account_id=self.account_id,
                kind=kind,
                stance=stance,
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

    async def by_external_id(self, external_id: str) -> Source | None:
        """Материал по id внешнего поста X (первый по времени создания)."""
        stmt = (
            self.scoped()
            .where(Source.external_id == external_id)
            .order_by(Source.created_at)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

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

    async def set_kind_for_batch(self, batch_id: str, *, kind: str, stance: str) -> int:
        """Сменить вид и отношение у обработанных источников партии; вернуть число изменённых."""
        stmt = (
            update(Source)
            .where(
                Source.account_id == self.account_id,
                Source.meta["batch"].astext == batch_id,
                Source.status == "indexed",
            )
            .values(kind=kind, stance=stance)
            .returning(Source.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self.session.execute(stmt)).all())

    async def count_documents(
        self, *, status: str, kind: str | None = None, stance: str | None = None
    ) -> int:
        """Число документов у источников аккаунта данного статуса (и вида/отношения)."""
        stmt = (
            select(func.count(Document.id))
            .join(Source, Source.id == Document.source_id)
            .where(Source.account_id == self.account_id, Source.status == status)
        )
        if kind is not None:
            stmt = stmt.where(Source.kind == kind)
        if stance is not None:
            stmt = stmt.where(Source.stance == stance)
        return int((await self.session.execute(stmt)).scalar_one())

    # --- ТЗ v3.1 ------------------------------------------------------------------------

    async def set_parent(
        self, source_id: UUID, parent_id: UUID | None, *, thread_root_id: UUID | None = None
    ) -> Source:
        """Связать ответ/цитату с исходным постом и тредом; чужие строки — NotFound."""
        for ref in (source_id, parent_id, thread_root_id):
            if ref is not None:
                await self.require(ref)
        values: dict[str, Any] = {"parent_source_id": parent_id}
        if thread_root_id is not None:
            values["thread_root_id"] = thread_root_id
        return await self.update(source_id, **values)

    async def set_stance_for_batch(self, batch_id: str, stance: str) -> int:
        """Сменить отношение у обработанных источников партии; вернуть число изменённых."""
        stmt = (
            update(Source)
            .where(
                Source.account_id == self.account_id,
                Source.meta["batch"].astext == batch_id,
                Source.status == "indexed",
            )
            .values(stance=stance)
            .returning(Source.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self.session.execute(stmt)).all())

    async def batch_ids(self, batch_id: str) -> list[UUID]:
        """Id источников партии (meta.batch) по порядку."""
        stmt = (
            select(Source.id)
            .where(Source.account_id == self.account_id, Source.meta["batch"].astext == batch_id)
            .order_by(Source.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def active_evidence(self, source_id: UUID) -> int:
        """Сколько неотозванных доказательств знаний опирается на материал."""
        stmt = select(func.count(KnowledgeEvidence.id)).where(
            KnowledgeEvidence.account_id == self.account_id,
            KnowledgeEvidence.source_id == source_id,
            KnowledgeEvidence.revoked_at.is_(None),
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def quarantine(self, source_id: UUID, reason: str) -> Source:
        """Отправить материал в карантин (разбор не делается)."""
        return await self.update(source_id, analysis_status="quarantined", quarantine_reason=reason)

    async def mark_removed(self, source_id: UUID, *, at: datetime) -> Source:
        """Владелец убрал материал: отметка, отзыв его доказательств, пересчёт весов знаний."""
        src = await self.update(source_id, removed_at=at)
        revoked = await self.session.execute(
            update(KnowledgeEvidence)
            .where(
                KnowledgeEvidence.account_id == self.account_id,
                KnowledgeEvidence.source_id == source_id,
                KnowledgeEvidence.revoked_at.is_(None),
            )
            .values(revoked_at=at, revoked_reason="source_removed")
            .returning(KnowledgeEvidence.item_id)
            .execution_options(synchronize_session=False)
        )
        for item_id in sorted(set(revoked.scalars().all())):
            await self.session.execute(
                text("SELECT knowledge_recompute_scores(:a, :i)"),
                {"a": self.account_id, "i": item_id},
            )
        return src
