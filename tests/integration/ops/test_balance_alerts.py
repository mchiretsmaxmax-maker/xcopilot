"""fix/deepseek-balance: «кончились деньги» вместо «очередь стоит», баланс заранее, /health."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from tests.integration.db.brain_helpers import source
from tests.integration.ops.conftest import OpsStand
from xcopilot.db.repos import LlmCallsRepo
from xcopilot.ops.alerts import AlertDeps, check_alerts
from xcopilot.ops.health import collect, render

EMPTY = "💳 Кончились деньги на DeepSeek — пополни баланс, задачи ждут"


async def _call(ops: OpsStand, minutes_ago: float, *, error: str | None) -> None:
    async with ops.uow() as uow:
        await LlmCallsRepo(uow.session, ops.account_id).log(
            ts=ops.clock.now() - timedelta(minutes=minutes_ago), role="cheap",
            provider="deepseek", model="deepseek-flash", purpose="brain.analyze",
            status="error" if error else "ok", error=error,
        )  # fmt: skip


async def _codes(deps: AlertDeps, ops: OpsStand) -> dict[str, str]:
    return {a.code: a.text for a in await check_alerts(deps, ops.account_id)}


async def test_402_gives_money_alert_instead_of_stale_queue(ops: OpsStand) -> None:
    async with ops.sf() as s:
        await source(
            s, ops.account_id, analysis_status="pending",
            created_at=ops.clock.now() - timedelta(minutes=90),
        )  # fmt: skip
        await s.commit()
    await _call(ops, 20, error="BalanceExhausted 402")
    found = await _codes(ops.deps, ops)
    assert found["balance_empty"].startswith(EMPTY)
    assert "analysis_queue_stale" not in found
    [alert] = [a for a in await check_alerts(ops.deps, ops.account_id) if a.code == "balance_empty"]
    assert alert.repeat == timedelta(hours=6)


async def test_money_alert_gone_after_successful_call(ops: OpsStand) -> None:
    await _call(ops, 20, error="BalanceExhausted 402")
    await _call(ops, 5, error=None)
    assert "balance_empty" not in await _codes(ops.deps, ops)


async def test_low_balance_checked_rarely_and_warned_daily(ops: OpsStand) -> None:
    calls: list[int] = []

    async def balance() -> float | None:
        calls.append(1)
        return 0.5

    deps = replace(ops.deps, balance=balance, balance_every=timedelta(hours=6), balance_min_usd=1.0)
    found = await _codes(deps, ops)
    assert "$0.50" in found["balance_low"]
    [low] = [a for a in await check_alerts(deps, ops.account_id) if a.code == "balance_low"]
    assert low.repeat == timedelta(hours=24)
    assert len(calls) == 1  # второй проход в пределах 6 часов берёт отметку, не API
    ops.clock.advance(6 * 3600 + 1)
    await _codes(deps, ops)
    assert len(calls) == 2


async def test_enough_balance_no_alert_and_health_line(ops: OpsStand) -> None:
    async def balance() -> float | None:
        return 2.5

    deps = replace(ops.deps, balance=balance)
    assert "balance_low" not in await _codes(deps, ops)
    report = await collect(ops.uow, ops.clock, ops.account_id)
    line = next(ln for ln in render(report).splitlines() if ln.startswith("Баланс DeepSeek"))
    assert "$2.50" in line


async def test_health_without_balance_data(ops: OpsStand) -> None:
    report = await collect(ops.uow, ops.clock, ops.account_id)
    assert "Баланс DeepSeek: нет данных" in render(report).splitlines()
