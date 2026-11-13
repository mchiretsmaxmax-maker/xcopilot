"""BudgetGuard: жёсткий стоп до вызова, мягкий порог 80% раз в период, изоляция аккаунтов."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest

from tests.integration.llm.conftest import Rig
from xcopilot.db.repos import BudgetsRepo, LlmCallsRepo
from xcopilot.errors import BudgetExceeded


async def spend(rig: Rig, account_id: UUID, micro: int, *, hours_ago: float = 0) -> None:
    async with rig.uow() as uow:
        await LlmCallsRepo(uow.session, account_id).log(
            ts=rig.clock.now() - timedelta(hours=hours_ago), role="cheap", provider="deepseek",
            model="deepseek-flash", purpose="seed", cost_micro_usd=micro, status="ok",
        )  # fmt: skip


async def set_limit(
    rig: Rig, account_id: UUID, period: str, limit: int, *, hard: bool = True
) -> None:
    async with rig.uow() as uow:
        await BudgetsRepo(uow.session, account_id).upsert(
            "llm", period, limit_micro_usd=limit, hard=hard
        )


async def events(rig: Rig, account_id: UUID, type_: str) -> list[dict[str, object]]:
    rows = await rig.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = :t ORDER BY ts, id",
        a=account_id,
        t=type_,
    )
    return [r.props for r in rows]


async def test_budget_hard_cap_raises_and_counts_estimate(rig: Rig) -> None:
    await set_limit(rig, rig.account_id, "day", 1_000_000)
    await spend(rig, rig.account_id, 600_000)
    await rig.budget.check(rig.account_id, "llm", 400_000)  # ровно лимит — можно
    with pytest.raises(BudgetExceeded):
        await rig.budget.check(rig.account_id, "llm", 400_001)
    with pytest.raises(BudgetExceeded):
        await rig.budget.check(rig.account_id, "llm", 500_000)
    # событие о превышении — одно за период, даже если блокировок несколько
    [ev] = await events(rig, rig.account_id, "budget.exceeded")
    assert ev == {"scope": "llm", "period": "day", "spent_micro_usd": 600_000,
                  "limit_micro_usd": 1_000_000}  # fmt: skip


async def test_budget_soft_threshold_emits_warning_once_per_day(rig: Rig) -> None:
    await set_limit(rig, rig.account_id, "day", 1_000_000)
    await spend(rig, rig.account_id, 700_000)
    await rig.budget.record(rig.account_id, "llm", 700_000)
    assert await events(rig, rig.account_id, "budget.warning") == []  # 70% — рано

    await spend(rig, rig.account_id, 150_000)
    await rig.budget.record(rig.account_id, "llm", 150_000)  # 85%
    await spend(rig, rig.account_id, 50_000)
    await rig.budget.record(rig.account_id, "llm", 50_000)  # 90% — второго нет
    [ev] = await events(rig, rig.account_id, "budget.warning")
    assert ev["spent_micro_usd"] == 850_000 and ev["period"] == "day"

    rig.clock.advance(24 * 3600)  # новый день — новое предупреждение
    await spend(rig, rig.account_id, 820_000)
    await rig.budget.record(rig.account_id, "llm", 820_000)
    assert len(await events(rig, rig.account_id, "budget.warning")) == 2


async def test_budget_day_follows_account_timezone(rig: Rig) -> None:
    # 12:00 UTC = 15:00 в Минске; трата в 22:30 UTC прошлого дня = 01:30 сегодня по Минску
    acc = await rig.new_account("minsk", tz="Europe/Minsk")
    await set_limit(rig, acc, "day", 1_000_000)
    await spend(rig, acc, 900_000, hours_ago=13.5)
    with pytest.raises(BudgetExceeded):
        await rig.budget.check(acc, "llm", 200_000)
    utc_acc = await rig.new_account("utc", tz="UTC")
    await set_limit(rig, utc_acc, "day", 1_000_000)
    await spend(rig, utc_acc, 900_000, hours_ago=13.5)  # по UTC — вчера
    await rig.budget.check(utc_acc, "llm", 200_000)


async def test_budget_soft_limit_warns_and_allows(rig: Rig) -> None:
    await set_limit(rig, rig.account_id, "month", 2_000_000, hard=False)
    await spend(rig, rig.account_id, 1_900_000, hours_ago=24 * 5)  # тот же месяц
    await rig.budget.check(rig.account_id, "llm", 300_000)  # не жёсткий — проходит
    [ev] = await events(rig, rig.account_id, "budget.warning")
    assert ev["period"] == "month" and ev["limit_micro_usd"] == 2_000_000
    assert await events(rig, rig.account_id, "budget.exceeded") == []


async def test_budget_default_limit_from_settings(rig: Rig) -> None:
    # строки budgets у аккаунта нет — действует дневной лимит по умолчанию ($5)
    await spend(rig, rig.account_id, 4_999_000)
    await rig.budget.check(rig.account_id, "llm", 1_000)
    with pytest.raises(BudgetExceeded):
        await rig.budget.check(rig.account_id, "llm", 1_001)


async def test_budget_isolated_per_account(rig: Rig) -> None:
    other = await rig.new_account("other")
    await set_limit(rig, rig.account_id, "day", 100_000)
    await set_limit(rig, other, "day", 100_000)
    await spend(rig, rig.account_id, 100_000)
    with pytest.raises(BudgetExceeded):
        await rig.budget.check(rig.account_id, "llm", 1)
    await rig.budget.check(other, "llm", 100_000)  # чужие траты не считаются
    await rig.budget.record(other, "llm", 0)
    assert await events(rig, other, "budget.exceeded") == []
    assert await events(rig, other, "budget.warning") == []


async def test_budget_x_api_spent_from_x_api_calls(rig: Rig) -> None:
    from sqlalchemy import text

    async with rig.uow() as uow:
        await BudgetsRepo(uow.session, rig.account_id).upsert(
            "x_api", "day", limit_micro_usd=10_000
        )
        await uow.session.execute(
            text(
                "INSERT INTO x_api_calls (account_id, ts, endpoint, resources, cost_micro_usd,"
                " utc_day, status) VALUES (:a, :ts, 'GET /2/users/:id/tweets', 9, 9000, :d, 'ok')"
            ),
            {"a": rig.account_id, "ts": rig.clock.now(), "d": rig.clock.now().date()},
        )
    await rig.budget.check(rig.account_id, "x_api", 1_000)
    with pytest.raises(BudgetExceeded):
        await rig.budget.check(rig.account_id, "x_api", 1_001)
    await rig.budget.check(rig.account_id, "llm", 1_000_000)  # llm считается отдельно
