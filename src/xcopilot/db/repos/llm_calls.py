"""Журнал вызовов моделей (только добавление)."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert

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
