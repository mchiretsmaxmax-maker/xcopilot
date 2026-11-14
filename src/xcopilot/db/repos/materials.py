"""Разбор материалов (ТЗ v3.1): словари, разборы, сигналы, исходы.

Разборы и исходы — производные с версиями: новая строка становится текущей (is_current),
прежняя остаётся в истории.
"""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID

from sqlalchemy import text, update

from xcopilot.db.models import MaterialAnalysis, MaterialOutcome, MaterialSignal, TaxonomyTerm
from xcopilot.db.repos.base import TenantRepo


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


class MaterialOutcomesRepo(_CurrentRepo[MaterialOutcome]):
    model = MaterialOutcome


class MaterialSignalsRepo(TenantRepo[MaterialSignal]):
    """Наблюдения метрик материала (только добавление)."""

    model = MaterialSignal

    async def best(self, source_id: UUID) -> MaterialSignal | None:
        """Лучший сигнал (представление v_material_best_signal): надёжность, затем свежесть."""
        row = await self.session.execute(
            text("SELECT id FROM v_material_best_signal WHERE account_id = :a AND source_id = :s"),
            {"a": self.account_id, "s": source_id},
        )
        best_id = row.scalar_one_or_none()
        return None if best_id is None else await self.get(best_id)
