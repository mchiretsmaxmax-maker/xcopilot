"""Журнал вызовов X API (только добавление)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, insert, select

from xcopilot.db.models import XApiCall
from xcopilot.db.repos.base import TenantRepo


class XApiCallsRepo(TenantRepo[XApiCall]):
    model = XApiCall

    async def log(
        self,
        *,
        endpoint: str,
        resources: int,
        cost_micro_usd: int,
        utc_day: date,
        http_status: int | None,
        status: str,
    ) -> None:
        """Добавить строку журнала в транзакцию вызывающего."""
        await self.session.execute(
            insert(XApiCall).values(
                account_id=self.account_id,
                endpoint=endpoint,
                resources=resources,
                cost_micro_usd=cost_micro_usd,
                utc_day=utc_day,
                http_status=http_status,
                status=status,
            )
        )

    async def spent_on_day(self, utc_day: date) -> int:
        """Сумма cost_micro_usd своего аккаунта за UTC-день."""
        stmt = select(func.coalesce(func.sum(XApiCall.cost_micro_usd), 0)).where(
            XApiCall.account_id == self.account_id, XApiCall.utc_day == utc_day
        )
        return int((await self.session.execute(stmt)).scalar_one())
