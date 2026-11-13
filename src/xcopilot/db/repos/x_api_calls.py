"""Журнал вызовов X API (только добавление)."""

from __future__ import annotations

from datetime import date

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
        raise NotImplementedError

    async def spent_on_day(self, utc_day: date) -> int:
        """Сумма cost_micro_usd своего аккаунта за UTC-день."""
        raise NotImplementedError
