"""Разбор материалов (ТЗ v3.1): словари, разборы, сигналы, исходы.

Разборы и исходы — производные с версиями: новая строка становится текущей (is_current),
прежняя остаётся в истории.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from xcopilot.db.models import (
    Account,
    MaterialAnalysis,
    MaterialOutcome,
    MaterialSignal,
    Source,
    TaxonomyTerm,
)
from xcopilot.db.repos.base import TenantRepo
from xcopilot.db.repos.sources import usable


@dataclass(frozen=True)
class BaselineRow:
    """Свой пост формата с лучшими точными метриками (own_sync / x_api)."""

    source_id: UUID
    views: int | None
    likes: int | None
    reposts: int | None
    quotes: int | None
    replies: int | None
    bookmarks: int | None


class TaxonomyRepo(TenantRepo[TaxonomyTerm]):
    model = TaxonomyTerm

    async def active(self, facet: str) -> builtins.list[TaxonomyTerm]:
        """Действующие значения словаря аккаунта, по ключу."""
        stmt = (
            self.scoped()
            .where(TaxonomyTerm.facet == facet, TaxonomyTerm.status == "active")
            .order_by(TaxonomyTerm.key)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    def _visible(self) -> Any:
        global_id = select(Account.id).where(Account.kind == "global").scalar_subquery()
        return select(TaxonomyTerm).where(
            or_(TaxonomyTerm.account_id == self.account_id, TaxonomyTerm.account_id == global_id)
        )

    async def visible(self, facet: str, status: str = "active") -> builtins.list[TaxonomyTerm]:
        """Значения словаря, видимые аккаунту: общие (global) и свои, по ключу.

        Своё значение с тем же ключом перекрывает общее.
        """
        stmt = (
            self._visible()
            .where(TaxonomyTerm.facet == facet, TaxonomyTerm.status == status)
            .order_by(TaxonomyTerm.key, TaxonomyTerm.account_id == self.account_id)
        )
        by_key: dict[str, TaxonomyTerm] = {}
        for term in (await self.session.execute(stmt)).scalars().all():
            by_key[term.key] = term  # своё идёт последним и перекрывает общее
        return builtins.list(by_key.values())

    async def max_version(self) -> int:
        """Версия таксономии аккаунта: наибольшая since_version действующих значений."""
        sub = self._visible().where(TaxonomyTerm.status == "active").subquery()
        stmt = select(func.coalesce(func.max(sub.c.since_version), 1))
        return int((await self.session.execute(stmt)).scalar_one())

    async def by_key(self, facet: str, key: str) -> TaxonomyTerm | None:
        """Своё значение аккаунта (любого статуса) по словарю и ключу."""
        stmt = self.scoped().where(TaxonomyTerm.facet == facet, TaxonomyTerm.key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def by_status(self, status: str) -> builtins.list[TaxonomyTerm]:
        stmt = (
            self.scoped()
            .where(TaxonomyTerm.status == status)
            .order_by(TaxonomyTerm.facet, TaxonomyTerm.key)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def fill_seed(self, facet: str, key: str, title: str, description: str) -> bool:
        """Стартовое значение (репозиторий системного аккаунта): вставить, если его нет;
        дописать пустое определение и название-заглушку (= ключ). True — вставлено сейчас."""
        stmt = (
            insert(TaxonomyTerm)
            .values(
                account_id=self.account_id,
                facet=facet,
                key=key,
                title=title,
                description=description,
                created_by="seed",
            )
            .on_conflict_do_nothing(index_elements=["account_id", "facet", "key"])
            .returning(TaxonomyTerm.id)
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is not None:
            return True
        base = update(TaxonomyTerm).where(
            TaxonomyTerm.account_id == self.account_id,
            TaxonomyTerm.facet == facet,
            TaxonomyTerm.key == key,
            TaxonomyTerm.created_by == "seed",
        )
        await self.session.execute(
            base.where(TaxonomyTerm.description == "").values(description=description)
        )
        await self.session.execute(
            base.where(TaxonomyTerm.title == TaxonomyTerm.key).values(title=title)
        )
        return False


class _CurrentRepo[M: MaterialAnalysis | MaterialOutcome](TenantRepo[M]):
    """Производная строка материала с одной текущей версией."""

    async def add_current(self, source_id: UUID, **fields: Any) -> M:
        """Сделать новую строку текущей; прежняя текущая получает is_current = false."""
        model = self.model
        await self.session.execute(
            update(model)
            .where(
                model.account_id == self.account_id,
                model.source_id == source_id,
                model.is_current.is_(True),
            )
            .values(is_current=False)
            .execution_options(synchronize_session=False)
        )
        return await self.add(source_id=source_id, is_current=True, **fields)

    async def current(self, source_id: UUID) -> M | None:
        model = self.model
        stmt = self.scoped().where(model.source_id == source_id, model.is_current.is_(True))
        return (await self.session.execute(stmt)).scalar_one_or_none()


class MaterialAnalysesRepo(_CurrentRepo[MaterialAnalysis]):
    model = MaterialAnalysis

    async def outdated(
        self, analyzer_version: str, taxonomy_version: int, limit: int
    ) -> builtins.list[UUID]:
        """Материалы, чей текущий разбор сделан старым промптом или старым словарём."""
        stmt = (
            select(MaterialAnalysis.source_id)
            .join(Source, Source.id == MaterialAnalysis.source_id)
            .where(
                MaterialAnalysis.account_id == self.account_id,
                MaterialAnalysis.is_current.is_(True),
                or_(
                    MaterialAnalysis.analyzer_version != analyzer_version,
                    MaterialAnalysis.taxonomy_version < taxonomy_version,
                ),
                usable(),
            )
            .order_by(MaterialAnalysis.created_at, MaterialAnalysis.id)
            .limit(limit)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())


class MaterialOutcomesRepo(_CurrentRepo[MaterialOutcome]):
    model = MaterialOutcome

    async def own_baseline(self, fmt: str, since: datetime) -> builtins.list[BaselineRow]:
        """Свои посты формата `fmt` с `posted_at >= since` и их лучшими точными метриками."""
        rows = await self.session.execute(
            text(
                "SELECT s.id, b.views, b.likes, b.reposts, b.quotes, b.replies, b.bookmarks"
                " FROM sources s JOIN v_material_best_signal b ON b.source_id = s.id"
                " WHERE s.account_id = :a AND s.stance = 'own' AND s.kind = :f"
                " AND s.posted_at >= :since AND s.removed_at IS NULL"
                " AND s.analysis_status <> 'quarantined'"
                " AND b.origin IN ('own_sync', 'x_api') AND b.views IS NOT NULL"
            ),
            {"a": self.account_id, "f": fmt, "since": since},
        )
        return [BaselineRow(*r) for r in rows.all()]


class MaterialSignalsRepo(TenantRepo[MaterialSignal]):
    """Наблюдения метрик материала (только добавление)."""

    model = MaterialSignal

    async def latest(self, source_id: UUID, origin: str) -> MaterialSignal | None:
        """Последнее наблюдение материала из данного источника цифр."""
        stmt = (
            self.scoped()
            .where(MaterialSignal.source_id == source_id, MaterialSignal.origin == origin)
            .order_by(MaterialSignal.observed_at.desc(), MaterialSignal.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_for(self, source_id: UUID) -> int:
        stmt = select(func.count()).where(
            MaterialSignal.account_id == self.account_id, MaterialSignal.source_id == source_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def best(self, source_id: UUID) -> MaterialSignal | None:
        """Лучший сигнал (представление v_material_best_signal): надёжность, затем свежесть."""
        row = await self.session.execute(
            text("SELECT id FROM v_material_best_signal WHERE account_id = :a AND source_id = :s"),
            {"a": self.account_id, "s": source_id},
        )
        best_id = row.scalar_one_or_none()
        return None if best_id is None else await self.get(best_id)
