"""BudgetGuard + CostMeter этапа 6: расход x_api из x_api_calls без двойного счёта."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from xcopilot.clock import SystemClock
from xcopilot.db.repos import AccountsRepo, BudgetsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import BudgetExceeded
from xcopilot.llm.budget import BudgetGuard, period_window
from xcopilot.x import cost
from xcopilot.x.client import CostMeter
from xcopilot.x.cost import DailyDedup

ENDPOINT = "GET /2/users/me"


async def test_cost_meter_with_budget_guard_counts_once_and_blocks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = SystemClock()  # cost.record пишет ts по часам БД — берём настоящее время

    def uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    async with uow() as u:
        acc = (await AccountsRepo(u.session).create(handle="xmeter", tz="UTC")).id
        await BudgetsRepo(u.session, acc).upsert("x_api", "day", limit_micro_usd=2_500)
    guard = BudgetGuard(uow, clock, default_day_limits={"x_api": 1_000_000})
    meter = CostMeter(
        account_id=acc, kind=cost.OWNED_USER, budget=guard, uow_factory=uow, clock=clock,
        dedup=DailyDedup(),
    )  # fmt: skip

    for user_id in ("1", "2"):
        await meter.before(ENDPOINT, [user_id], 1)
        await meter.after(ENDPOINT, [user_id], 200, True)

    from xcopilot.db.repos import SpendRepo

    since, until = period_window("day", clock.now(), "UTC")
    async with uow() as u:
        spent = await SpendRepo(u.session, acc).spent("x_api", since, until)
    assert spent == 2 * cost.PRICES[cost.OWNED_USER]  # каждый запрос учтён ровно один раз

    with pytest.raises(BudgetExceeded):
        await meter.before(ENDPOINT, ["3"], 1)  # 2000 + 1000 > 2500 — запрос не уходит
