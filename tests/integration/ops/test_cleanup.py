"""AUD-009: ops.cleanup — ежедневная очистка старых задач и отметок алертов."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from tests.integration.db.brain_helpers import account
from tests.integration.ops.conftest import OpsStand
from xcopilot.db.repos.ops import OpsRepo
from xcopilot.jobs.handlers import KNOWN_KINDS
from xcopilot.jobs.queue import Job
from xcopilot.jobs.scheduler import DEFAULT_SCHEDULES

OLD = timedelta(days=31)
FRESH = timedelta(days=29)
CUTOFF = timedelta(days=30)


async def add_job(ops: OpsStand, account_id: UUID, status: str, age: timedelta) -> None:
    at = ops.clock.now() - age
    await ops.exec(
        "INSERT INTO jobs (account_id, kind, payload, status, attempts, max_attempts, run_at,"
        " finished_at) VALUES (:a, 'ops.alerts', '{}'::jsonb, :s, 1, 5, :t, :f)",
        a=account_id,
        s=status,
        t=at,
        f=at if status in ("done", "dead") else None,
    )


async def add_mark(ops: OpsStand, account_id: UUID, key: str, age: timedelta) -> None:
    await ops.exec(
        "INSERT INTO ops_marks (account_id, key, at) VALUES (:a, :k, :t)",
        a=account_id,
        k=key,
        t=ops.clock.now() - age,
    )


async def count(ops: OpsStand, table: str, account_id: UUID) -> int:
    return int(
        await ops.scalar(f"SELECT count(*) FROM {table} WHERE account_id = :a", a=account_id)
    )


async def test_cleanup_deletes_old_finished_jobs_only(ops: OpsStand) -> None:
    for status in ("done", "dead"):
        await add_job(ops, ops.account_id, status, OLD)  # старые — удалить
        await add_job(ops, ops.account_id, status, FRESH)  # свежие — остаются
    await add_job(ops, ops.account_id, "queued", OLD)
    await add_job(ops, ops.account_id, "running", OLD)
    async with ops.uow() as uow:
        deleted = await OpsRepo(uow.session).cleanup(ops.account_id, ops.clock.now() - CUTOFF)
    assert deleted == 2
    statuses = sorted(
        await ops.scalar(
            "SELECT array_agg(status ORDER BY status) FROM jobs WHERE account_id = :a",
            a=ops.account_id,
        )
    )
    assert statuses == ["dead", "done", "queued", "running"]


async def test_cleanup_deletes_old_job_dead_marks(ops: OpsStand) -> None:
    await add_mark(ops, ops.account_id, "alert:job_dead:old", OLD)
    await add_mark(ops, ops.account_id, "alert:job_dead:new", FRESH)
    await add_mark(ops, ops.account_id, "alert:budget_80", OLD)  # другой алерт — не трогаем
    async with ops.uow() as uow:
        deleted = await OpsRepo(uow.session).cleanup(ops.account_id, ops.clock.now() - CUTOFF)
    assert deleted == 1
    keys = await ops.scalar(
        "SELECT array_agg(key ORDER BY key) FROM ops_marks WHERE account_id = :a",
        a=ops.account_id,
    )
    assert keys == ["alert:budget_80", "alert:job_dead:new"]


async def test_cleanup_leaves_other_account_alone(ops: OpsStand) -> None:
    async with ops.sf() as s:
        other = await account(s, handle="other_acc")
        await s.commit()
    await add_job(ops, other, "done", OLD)
    await add_mark(ops, other, "alert:job_dead:x", OLD)
    async with ops.uow() as uow:
        await OpsRepo(uow.session).cleanup(ops.account_id, ops.clock.now() - CUTOFF)
    assert await count(ops, "jobs", other) == 1
    assert await count(ops, "ops_marks", other) == 1


async def test_cleanup_does_not_touch_events(ops: OpsStand) -> None:
    before = await ops.scalar("SELECT count(*) FROM events")
    async with ops.uow() as uow:
        await OpsRepo(uow.session).cleanup(ops.account_id, ops.clock.now())
    assert await ops.scalar("SELECT count(*) FROM events") == before


async def test_cleanup_handler_returns_deleted_count(ops: OpsStand) -> None:
    from xcopilot.ops.cleanup import RETENTION, handle_ops_cleanup

    assert timedelta(days=30) == RETENTION
    await add_job(ops, ops.account_id, "done", OLD)
    await add_mark(ops, ops.account_id, "alert:job_dead:old", OLD)
    now = ops.clock.now()
    job = Job(uuid4(), ops.account_id, "ops.cleanup", {}, "running", 1, 5, now, "w", None)
    deleted = await handle_ops_cleanup(job, uow_factory=ops.uow, clock=ops.clock)
    assert deleted == 2


def test_cleanup_is_scheduled_daily_at_0430() -> None:
    sched = {d.kind: d for d in DEFAULT_SCHEDULES}
    assert "ops.cleanup" in KNOWN_KINDS
    assert sched["ops.cleanup"].at_local_time.isoformat() == "04:30:00"
    assert sched["ops.cleanup"].weekday is None
