"""fix/bot-control: честные счётчики алерта analysis_queue_stale и /health."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from tests.integration.db.brain_helpers import source
from tests.integration.ops.conftest import OpsStand
from xcopilot.ops.alerts import check_alerts
from xcopilot.ops.health import collect, render


async def _old(ops: OpsStand, minutes: int, **extra: Any) -> None:
    async with ops.sf() as s:
        await source(
            s, ops.account_id, analysis_status="pending",
            created_at=ops.clock.now() - timedelta(minutes=minutes), **extra,
        )  # fmt: skip
        await s.commit()


async def test_excluded_and_failed_sources_are_not_in_analysis_queue(ops: OpsStand) -> None:
    await ops.exec(
        "INSERT INTO ops_marks (account_id, key, at)"
        " SELECT id, 'backup.ok', :at FROM accounts WHERE kind = 'global'",
        at=ops.clock.now(),
    )
    await _old(ops, 300, status="excluded")
    await _old(ops, 300, status="failed")
    codes = {a.code for a in await check_alerts(ops.deps, ops.account_id)}
    assert "analysis_queue_stale" not in codes
    report = await collect(ops.uow, ops.clock, ops.account_id)
    assert (report.analysis_waiting, report.analysis_stale) == (0, 0)

    await _old(ops, 90, status="indexed")  # настоящая очередь
    await _old(ops, 10, status="indexed")
    report = await collect(ops.uow, ops.clock, ops.account_id)
    assert (report.analysis_waiting, report.analysis_stale) == (2, 1)
    codes = {a.code for a in await check_alerts(ops.deps, ops.account_id)}
    assert "analysis_queue_stale" in codes


async def test_health_separates_due_jobs_from_scheduled(ops: OpsStand) -> None:
    now = ops.clock.now()
    for run_at in (
        now - timedelta(minutes=10),
        now - timedelta(minutes=1),
        now + timedelta(hours=5),
    ):
        await ops.exec(
            "INSERT INTO jobs (account_id, kind, payload, status, run_at)"
            " VALUES (:a, 'writer.topics', '{}'::jsonb, 'queued', :r)",
            a=ops.account_id, r=run_at,
        )  # fmt: skip
    report = await collect(ops.uow, ops.clock, ops.account_id)
    assert report.queue is not None
    assert (report.queue.queued, report.queue.scheduled) == (2, 1)
    # старейшая — среди ждущих сейчас, а не плановая задача через 5 часов
    assert report.queue.oldest_queued == now - timedelta(minutes=10)
    line = next(ln for ln in render(report).splitlines() if ln.startswith("Очередь задач"))
    assert "ждут сейчас 2" in line and "запланировано 1" in line


async def test_health_with_only_scheduled_jobs_has_no_oldest(ops: OpsStand) -> None:
    await ops.exec(
        "INSERT INTO jobs (account_id, kind, payload, status, run_at)"
        " VALUES (:a, 'metrics.rollup', '{}'::jsonb, 'queued', :r)",
        a=ops.account_id, r=ops.clock.now() + timedelta(hours=1),
    )  # fmt: skip
    report = await collect(ops.uow, ops.clock, ops.account_id)
    assert report.queue is not None and report.queue.queued == 0
    assert report.queue.oldest_queued is None
    line = next(ln for ln in render(report).splitlines() if ln.startswith("Очередь задач"))
    assert "ждут сейчас 0" in line and "запланировано 1" in line and "старейшая" not in line
