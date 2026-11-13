"""Расход за период по журналам вызовов и отметки бюджета в событиях (для BudgetGuard)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Event, LlmCall, XApiCall

_JOURNALS: dict[str, type[LlmCall] | type[XApiCall]] = {"llm": LlmCall, "x_api": XApiCall}


class SpendRepo:
    """Только чтение; всё ограничено своим account_id."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("SpendRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    async def spent(self, scope: str, since: datetime, until: datetime) -> int:
        """Сумма cost_micro_usd за [since, until): llm → llm_calls, x_api → x_api_calls."""
        journal = _JOURNALS.get(scope)
        if journal is None:
            raise ValueError(f"неизвестный scope бюджета: {scope}")
        stmt = select(func.coalesce(func.sum(journal.cost_micro_usd), 0)).where(
            journal.account_id == self.account_id, journal.ts >= since, journal.ts < until
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def budget_events(
        self, type_: str, scope: str, period: str, since: datetime, until: datetime
    ) -> int:
        """Сколько событий type_ с props.scope/props.period было в [since, until)."""
        stmt = select(func.count()).where(
            Event.account_id == self.account_id,
            Event.type == type_,
            Event.ts >= since,
            Event.ts < until,
            Event.props["scope"].astext == scope,
            Event.props["period"].astext == period,
        )
        return int((await self.session.execute(stmt)).scalar_one())
