"""Журнал вызовов моделей (только добавление)."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select

from xcopilot.db.models import LlmCall
from xcopilot.db.repos.base import TenantRepo


class LlmCallsRepo(TenantRepo[LlmCall]):
    model = LlmCall

    async def log(self, *, ts: datetime, **values: Any) -> UUID:
        """Добавить строку журнала в текущую транзакцию; вернуть её id."""
        if "account_id" in values:
            raise ValueError("account_id задаёт репозиторий")
        stmt = (
            insert(LlmCall)
            .values(account_id=self.account_id, ts=ts, **values)
            .returning(LlmCall.id)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def for_entity(self, entity_type: str, entity_id: UUID) -> builtins.list[LlmCall]:
        """Вызовы ради сущности (например, все вызовы по черновику) в порядке времени."""
        stmt = (
            self.scoped()
            .where(LlmCall.entity_type == entity_type, LlmCall.entity_id == entity_id)
            .order_by(LlmCall.ts, LlmCall.id)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def latest_for(self, entity_type: str, entity_id: UUID, purpose: str) -> LlmCall | None:
        """Последний успешный вызов ради сущности с данной целью."""
        stmt = (
            self.scoped()
            .where(
                LlmCall.entity_type == entity_type,
                LlmCall.entity_id == entity_id,
                LlmCall.purpose == purpose,
                LlmCall.status == "ok",
            )
            .order_by(LlmCall.ts.desc(), LlmCall.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def spent(self, purpose: str, since: datetime) -> int:
        """Сумма cost_micro_usd вызовов с данной целью начиная с `since`."""
        stmt = select(func.coalesce(func.sum(LlmCall.cost_micro_usd), 0)).where(
            LlmCall.account_id == self.account_id, LlmCall.purpose == purpose, LlmCall.ts >= since
        )
        return int((await self.session.execute(stmt)).scalar_one())
