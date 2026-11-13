"""Расход на модели и X за период (чтение журналов `llm_calls` / `x_api_calls`)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from xcopilot.db.models import LlmCall, XApiCall
from xcopilot.db.repos.base import TenantRepo


class UsageRepo(TenantRepo[LlmCall]):
    model = LlmCall

    async def llm_spend_by_role(self, start: datetime, end: datetime) -> dict[str, int]:
        """Расход на модели по ролям за [start, end), микродоллары."""
        stmt = (
            select(LlmCall.role, func.coalesce(func.sum(LlmCall.cost_micro_usd), 0))
            .where(
                LlmCall.account_id == self.account_id,
                LlmCall.ts >= start,
                LlmCall.ts < end,
            )
            .group_by(LlmCall.role)
        )
        return {role: int(total) for role, total in (await self.session.execute(stmt)).all()}

    async def x_spend(self, start: datetime, end: datetime) -> int:
        """Расход на X API за [start, end), микродоллары."""
        stmt = select(func.coalesce(func.sum(XApiCall.cost_micro_usd), 0)).where(
            XApiCall.account_id == self.account_id,
            XApiCall.ts >= start,
            XApiCall.ts < end,
        )
        return int((await self.session.execute(stmt)).scalar_one())
