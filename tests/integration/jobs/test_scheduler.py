"""Планировщик (fix/worker-app-wiring): задачи по таблице schedules без дублей."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.scheduler import DEFAULT_SCHEDULES, Scheduler

SF = async_sessionmaker[AsyncSession]


async def _count(sf: SF, table: str, where: str = "true") -> int:
    async with sf() as s:
        return int(
            (await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"))).scalar_one()
        )


async def _exec(sf: SF, sql: str) -> None:
    async with sf() as s:
        await s.execute(text(sql))
        await s.commit()


def _scheduler(sf: SF, clock: FakeClock) -> Scheduler:
    return Scheduler(lambda: UnitOfWork(sf), clock)


async def test_scheduler_does_not_enqueue_twice(session_factory: SF) -> None:
    # засеянное расписание db.ensure_partitions — 03:00 по поясу системного аккаунта (Минск)
    clock = FakeClock(datetime(2026, 3, 9, 23, 0, tzinfo=UTC))  # в Минске 02:00
    sched = _scheduler(session_factory, clock)
    await sched.tick()
    assert await _count(session_factory, "jobs", "kind = 'db.ensure_partitions'") == 0
    clock.advance(2 * 3600)  # 04:00 в Минске
    first = await sched.tick()
    assert len(first) == 1
    await sched.tick()
    assert await _count(session_factory, "jobs", "kind = 'db.ensure_partitions'") == 1
    # даже если отметка запуска потерялась, ключ идемпотентности не даёт дубля
    await _exec(session_factory, "UPDATE schedules SET last_run_at = NULL")
    await sched.tick()
    assert await _count(session_factory, "jobs", "kind = 'db.ensure_partitions'") == 1
    clock.advance(24 * 3600)  # следующий день
    await sched.tick()
    assert await _count(session_factory, "jobs", "kind = 'db.ensure_partitions'") == 2


async def test_disabled_schedule_is_skipped(session_factory: SF) -> None:
    await _exec(session_factory, "UPDATE schedules SET enabled = false")
    clock = FakeClock(datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
    assert await _scheduler(session_factory, clock).tick() == []
    assert await _count(session_factory, "jobs") == 0


async def test_default_schedules_created_for_each_account(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await AccountsRepo(uow.session).create(handle="sched", tz="UTC")
        acc_id: UUID = acc.id
    kinds = {d.kind for d in DEFAULT_SCHEDULES}
    assert "metrics.rollup" in kinds
    clock = FakeClock(datetime(2026, 3, 10, 12, 0, tzinfo=UTC))
    sched = _scheduler(session_factory, clock)
    await sched.tick()
    await sched.tick()
    where = f"account_id = '{acc_id}'"
    assert await _count(session_factory, "schedules", where) == len(DEFAULT_SCHEDULES)
    # полдень UTC: все ежедневные расписания аккаунта уже наступили — по одной задаче
    rollups = f"{where} AND kind = 'metrics.rollup'"
    assert await _count(session_factory, "jobs", rollups) == 1
    async with session_factory() as s:
        global_scheds = await s.execute(
            text(
                "SELECT count(*) FROM schedules s JOIN accounts a ON a.id = s.account_id"
                " WHERE a.kind = 'global' AND s.kind = 'metrics.rollup'"
            )
        )
        assert global_scheds.scalar_one() == 0  # у системного аккаунта свёрток нет
