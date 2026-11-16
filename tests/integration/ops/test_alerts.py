"""Этап 15: алерты владельцу (`ops.alerts`) — что считается поломкой и как часто о ней говорить."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from tests.integration.db.brain_helpers import item, source
from tests.integration.ops.conftest import OpsStand
from xcopilot.db.repos import LlmCallsRepo
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.ops.alerts import check_alerts, send_alerts


async def codes(ops: OpsStand) -> dict[str, str]:
    return {a.code: a.text for a in await check_alerts(ops.deps, ops.account_id)}


async def mark_backup(ops: OpsStand, hours_ago: float) -> None:
    await ops.exec(
        "INSERT INTO ops_marks (account_id, key, at)"
        " SELECT id, 'backup.ok', :at FROM accounts WHERE kind = 'global'"
        " ON CONFLICT (account_id, key) DO UPDATE SET at = EXCLUDED.at",
        at=ops.clock.now() - timedelta(hours=hours_ago),
    )


async def dead_job(ops: OpsStand, kind: str = "writer.generate", **over: Any) -> None:
    values: dict[str, Any] = {
        "a": ops.account_id,
        "k": kind,
        "f": ops.clock.now() - timedelta(minutes=5),
    }
    values.update(over)
    await ops.exec(
        "INSERT INTO jobs (account_id, kind, payload, status, attempts, max_attempts, run_at,"
        " finished_at, last_error) VALUES (:a, :k, '{}'::jsonb, 'dead', 5, 5, :f, :f, 'boom')",
        **values,
    )


async def test_alert_on_dead_job(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    assert "job_dead" not in await codes(ops)
    await dead_job(ops)
    found = await codes(ops)
    assert "job_dead" in found
    msg = found["job_dead"]
    assert msg.startswith("⚠️ Задача writer.generate не выполнена после 5 попыток (код E-")
    assert "boom" not in msg  # текст ошибки — в лог, владельцу — код


async def test_alert_on_budget_80(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    async with ops.uow() as uow:
        await LlmCallsRepo(uow.session, ops.account_id).log(
            ts=ops.clock.now(), role="cheap", provider="deepseek", model="deepseek-flash",
            purpose="seed", cost_micro_usd=700_000, status="ok",
        )  # fmt: skip
    assert "budget_80" not in await codes(ops)
    async with ops.uow() as uow:
        await LlmCallsRepo(uow.session, ops.account_id).log(
            ts=ops.clock.now(), role="cheap", provider="deepseek", model="deepseek-flash",
            purpose="seed", cost_micro_usd=150_000, status="ok",
        )  # fmt: skip
    found = await codes(ops)
    assert "budget_80" in found
    assert "85%" in found["budget_80"]


async def test_alert_on_open_circuit_15_min(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    ops.breakers["deepseek"] = ops.clock.now() - timedelta(minutes=10)
    ops.breakers["anthropic"] = None
    assert "circuit_open" not in await codes(ops)  # 10 минут — ещё может само пройти
    ops.clock.advance(6 * 60)
    found = await codes(ops)
    assert "circuit_open" in found
    assert "deepseek" in found["circuit_open"] and "anthropic" not in found["circuit_open"]


async def test_alert_on_default_partition_rows(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    await ops.exec("DELETE FROM events_default")
    assert "default_partition" not in await codes(ops)
    await ops.exec(
        "INSERT INTO events (account_id, ts, type, actor, props)"
        " VALUES (:a, :t, 'test.x', 'system', '{}'::jsonb)",
        a=ops.account_id,
        t=ops.clock.now(),
    )
    found = await codes(ops)
    assert "default_partition" in found
    assert "events" in found["default_partition"]


async def test_alert_on_stale_backup(ops: OpsStand) -> None:
    found = await codes(ops)
    assert "backup_stale" in found  # ни одного бэкапа
    await mark_backup(ops, 2)
    assert "backup_stale" not in await codes(ops)
    await mark_backup(ops, 30)
    assert "backup_stale" in await codes(ops)


async def test_alerts_not_repeated_within_hour(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    await ops.exec("DELETE FROM events_default")
    ops.breakers["deepseek"] = ops.clock.now() - timedelta(minutes=20)
    sent = await send_alerts(ops.deps, ops.account_id, ops.inbox)
    assert [a.code for a in sent] == ["circuit_open"]
    assert len(ops.inbox.sent) == 1
    ops.clock.advance(30 * 60)
    assert await send_alerts(ops.deps, ops.account_id, ops.inbox) == []
    ops.clock.advance(31 * 60)
    await mark_backup(ops, 1)
    again = await send_alerts(ops.deps, ops.account_id, ops.inbox)
    assert [a.code for a in again] == ["circuit_open"]
    assert len(ops.inbox.sent) == 2


async def test_alert_on_voice_drift(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    async with ops.sf() as s:
        await sync_catalog(s)
        await s.commit()
    for week, value in ((date(2025, 12, 1), 0.10), (date(2025, 12, 22), 0.11)):
        await ops.exec(
            "INSERT INTO metric_values (account_id, metric_key, grain, period_start, dims,"
            " dims_hash, value, sample_size, definition_version, computed_at)"
            " VALUES (:a, 'brain_voice_drift', 'week', :p, '{}'::jsonb, 'h', :v, 5, 1, :t)",
            a=ops.account_id,
            p=week,
            v=value,
            t=ops.clock.now(),
        )
    assert "voice_drift" not in await codes(ops)  # +10% — в пределах нормы
    await ops.exec(
        "UPDATE metric_values SET value = 0.14 WHERE account_id = :a AND period_start = :p",
        a=ops.account_id,
        p=date(2025, 12, 22),
    )
    found = await codes(ops)
    assert "voice_drift" in found and "голос" in found["voice_drift"].lower()


async def test_alert_on_high_auto_revert_rate(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    async with ops.sf() as s:
        items = [await item(s, ops.account_id, status="probation") for _ in range(5)]
        await s.commit()

    async def transition(i: int, reverted: bool) -> None:
        when = ops.clock.now() - timedelta(days=2)
        await ops.exec(
            "INSERT INTO knowledge_transitions (account_id, item_id, ts, from_status, to_status,"
            " actor, rule, payload, reverted_at) VALUES (:a, :i, :t, 'candidate', 'probation',"
            " 'system', 'auto_probation', '{}'::jsonb, :r)",
            a=ops.account_id,
            i=items[i],
            t=when,
            r=when + timedelta(hours=3) if reverted else None,
        )

    for i in range(4):
        await transition(i, reverted=i == 0)
    assert "auto_revert_high" not in await codes(ops)  # 1 из 4 = 0.25
    await transition(4, reverted=True)
    found = await codes(ops)
    assert "auto_revert_high" in found  # 2 из 5 = 0.4 > 0.3
    assert "40%" in found["auto_revert_high"]


async def test_alert_on_stale_analysis_queue(ops: OpsStand) -> None:
    await mark_backup(ops, 1)
    async with ops.sf() as s:
        await source(
            s, ops.account_id, analysis_status="pending",
            created_at=ops.clock.now() - timedelta(minutes=20),
        )  # fmt: skip
        await s.commit()
    assert "analysis_queue_stale" not in await codes(ops)
    async with ops.sf() as s:
        await source(
            s, ops.account_id, analysis_status="pending",
            created_at=ops.clock.now() - timedelta(minutes=90),
        )  # fmt: skip
        await s.commit()
    found = await codes(ops)
    assert "analysis_queue_stale" in found
    assert "1" in found["analysis_queue_stale"]


async def test_no_backup_alert_in_dev(ops: OpsStand) -> None:
    """fix/bot-ux-progress: на dev бэкапа нет и не будет — не шуметь."""
    from dataclasses import replace

    deps = replace(ops.deps, env="dev")
    assert "backup_stale" not in {a.code for a in await check_alerts(deps, ops.account_id)}
    assert "backup_stale" in await codes(ops)  # прод по умолчанию — как раньше
