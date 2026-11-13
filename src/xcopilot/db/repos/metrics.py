"""События, каталог метрик и свёртки: весь SQL подсистемы метрик.

`EventsRepo` пишет события в транзакцию вызывающего. `MetricDefsRepo` — глобальный каталог.
`MetricValuesRepo` — свёртки аккаунта и исполнение формул в транзакции READ ONLY
(внутри SAVEPOINT: `SET LOCAL transaction_read_only` откатывается вместе с ним).
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, insert, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Event, MetricValue
from xcopilot.db.models import MetricDefinition as MetricDefinitionRow
from xcopilot.db.repos.base import TenantRepo
from xcopilot.errors import NotFound
from xcopilot.metrics.errors import FormulaExecutionError


class EventsRepo(TenantRepo[Event]):
    model = Event

    async def insert_event(
        self,
        *,
        type_: str,
        actor: str,
        user_id: UUID | None,
        entity_type: str | None,
        entity_id: UUID | None,
        props: dict[str, Any],
        ts: datetime | None,
    ) -> UUID:
        """Добавить событие в текущую транзакцию; вернуть его id."""
        values: dict[str, Any] = {
            "account_id": self.account_id,
            "type": type_,
            "actor": actor,
            "user_id": user_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "props": props,
        }
        if ts is not None:
            values["ts"] = ts
        result = await self.session.execute(insert(Event).values(**values).returning(Event.id))
        return result.scalar_one()


class MetricDefsRepo:
    """Каталог метрик (глобальный, без account_id)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def all(self) -> builtins.list[MetricDefinitionRow]:
        stmt = select(MetricDefinitionRow).order_by(MetricDefinitionRow.key)
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def active(self) -> builtins.list[MetricDefinitionRow]:
        return [d for d in await self.all() if d.active]

    async def get(self, key: str) -> MetricDefinitionRow | None:
        stmt = select(MetricDefinitionRow).where(MetricDefinitionRow.key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def require(self, key: str) -> MetricDefinitionRow:
        row = await self.get(key)
        if row is None:
            raise NotFound(f"метрика {key!r} не найдена в каталоге")
        return row

    async def add(self, **values: Any) -> MetricDefinitionRow:
        row = MetricDefinitionRow(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, key: str, **values: Any) -> None:
        row = await self.require(key)
        for name, value in values.items():
            setattr(row, name, value)
        await self.session.flush()


class MetricValuesRepo:
    """Свёртки аккаунта и исполнение формул."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def run_formula(
        self, sql: str, params: Mapping[str, Any]
    ) -> builtins.list[dict[str, Any]]:
        """Выполнить формулу в транзакции READ ONLY; записи и DDL в ней невозможны."""
        nested = await self.session.begin_nested()
        try:
            await self.session.execute(text("SET LOCAL transaction_read_only = on"))
            result = await self.session.execute(text(sql), dict(params))
            rows = [dict(r) for r in result.mappings().all()]
        except DBAPIError as exc:
            raise FormulaExecutionError(f"формула не выполнилась: {exc.orig}") from exc
        finally:
            await nested.rollback()
        for row in rows:
            dims = row.get("dims")
            if isinstance(dims, str):
                row["dims"] = json.loads(dims)
        return rows

    async def upsert(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Записать свёртки; неизменившиеся строки не трогаются (computed_at сохраняется)."""
        if not rows:
            return 0
        stmt = pg_insert(MetricValue).values(
            [{"account_id": self.account_id, **row} for row in rows]
        )
        new = stmt.excluded
        cur = MetricValue.__table__.c
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_id", "metric_key", "grain", "period_start", "dims_hash"],
            set_={
                "value": new.value,
                "sample_size": new.sample_size,
                "definition_version": new.definition_version,
                "dims": new.dims,
                "computed_at": new.computed_at,
            },
            where=(
                cur.value.is_distinct_from(new.value)
                | (cur.sample_size != new.sample_size)
                | (cur.definition_version != new.definition_version)
            ),
        )
        await self.session.execute(stmt)
        return len(rows)

    async def delete_missing(
        self,
        key: str,
        grain: str,
        date_from: date,
        date_to: date,
        keep: Collection[tuple[date, str]],
    ) -> int:
        """Удалить свёртки диапазона, которых формула больше не выдаёт."""
        t = MetricValue.__table__.c
        stmt = select(t.period_start, t.dims_hash).where(
            t.account_id == self.account_id,
            t.metric_key == key,
            t.grain == grain,
            t.period_start >= date_from,
            t.period_start <= date_to,
        )
        existing = {(r[0], r[1]) for r in (await self.session.execute(stmt)).all()}
        stale = sorted(existing - set(keep))
        if not stale:
            return 0
        await self.session.execute(
            delete(MetricValue).where(
                t.account_id == self.account_id,
                t.metric_key == key,
                t.grain == grain,
                tuple_(t.period_start, t.dims_hash).in_(stale),
            )
        )
        return len(stale)

    async def stored(
        self,
        key: str,
        grain: str,
        date_from: date,
        date_to: date,
        *,
        dimension: str | None = None,
        dims_hash: str | None = None,
    ) -> builtins.list[MetricValue]:
        """Свёртки периодов date_from..date_to; по dims_hash или по наличию измерения."""
        stmt = select(MetricValue).where(
            MetricValue.account_id == self.account_id,
            MetricValue.metric_key == key,
            MetricValue.grain == grain,
            MetricValue.period_start >= date_from,
            MetricValue.period_start <= date_to,
        )
        if dims_hash is not None:
            stmt = stmt.where(MetricValue.dims_hash == dims_hash)
        if dimension is not None:
            stmt = stmt.where(MetricValue.dims.has_key(dimension))
        stmt = stmt.order_by(MetricValue.period_start, MetricValue.dims_hash)
        stmt = stmt.execution_options(populate_existing=True)
        return builtins.list((await self.session.execute(stmt)).scalars().all())
