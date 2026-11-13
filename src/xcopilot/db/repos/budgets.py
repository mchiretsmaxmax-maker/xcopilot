"""Лимиты расходов аккаунта."""

from __future__ import annotations

from xcopilot.db.models import Budget
from xcopilot.db.repos.base import TenantRepo


class BudgetsRepo(TenantRepo[Budget]):
    model = Budget

    async def get_limit(self, scope: str, period: str) -> Budget | None:
        """Строка лимита своего аккаунта по scope и period."""
        stmt = self.scoped().where(Budget.scope == scope, Budget.period == period)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self, scope: str, period: str, *, limit_micro_usd: int, hard: bool = True
    ) -> Budget:
        """Создать или обновить лимит своего аккаунта."""
        row = await self.get_limit(scope, period)
        if row is None:
            return await self.add(
                scope=scope, period=period, limit_micro_usd=limit_micro_usd, hard=hard
            )
        return await self.update(row.id, limit_micro_usd=limit_micro_usd, hard=hard)
