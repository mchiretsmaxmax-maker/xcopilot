"""Воркер очереди (fix/worker-app-wiring): выполнение, повтор с паузой, dead, остановка."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec, Worker

SF = async_sessionmaker[AsyncSession]


class Permanent(Exception):
    pass


async def _account(sf: SF, handle: str = "w") -> UUID:
    async with UnitOfWork(sf) as uow:
        acc = await AccountsRepo(uow.session).create(handle=handle, niche="Крипта", lang="ru")
        return acc.id


def _queue(sf: SF, clock: FakeClock, max_attempts: int = 5) -> JobQueue:
    return JobQueue(lambda: UnitOfWork(sf), clock, max_attempts=max_attempts)


async def _count(sf: SF, table: str, where: str = "true") -> int:
    async with sf() as s:
        return int(
            (await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"))).scalar_one()
        )


async def test_worker_runs_job_and_marks_done(session_factory: SF, clock: FakeClock) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)
    seen: list[Job] = []

    async def handle(job: Job) -> None:
        seen.append(job)

    worker = Worker(queue, {"test.ok": HandlerSpec(handle)})
    job_id = await queue.enqueue("test.ok", {"x": 1}, account_id=acc)
    assert await worker.run_once() is True
    assert [j.payload for j in seen] == [{"x": 1}]
    job = await queue.get(job_id)
    assert job is not None and job.status == "done"
    assert await worker.run_once() is False  # очередь пуста


async def test_failure_retries_with_backoff_then_dead(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock, max_attempts=2)
    calls = 0

    async def boom(job: Job) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("модель недоступна")

    worker = Worker(queue, {"test.boom": HandlerSpec(boom)})
    job_id = await queue.enqueue("test.boom", {}, account_id=acc)
    assert await worker.run_once() is True
    job = await queue.get(job_id)
    assert job is not None and job.status == "queued"
    assert job.run_at == clock.now() + queue.backoff(1)
    assert job.last_error is not None and "модель недоступна" in job.last_error
    assert await worker.run_once() is False  # пауза ещё не прошла
    clock.advance(queue.backoff(1).total_seconds() + 1)
    assert await worker.run_once() is True
    job = await queue.get(job_id)
    assert job is not None and job.status == "dead"
    assert calls == 2
    assert await _count(session_factory, "events", "type = 'job.dead'") == 1


async def test_permanent_error_is_dead_at_once(session_factory: SF, clock: FakeClock) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)

    async def bad(job: Job) -> None:
        raise Permanent("неверная задача")

    worker = Worker(queue, {"test.bad": HandlerSpec(bad, permanent=(Permanent,))})
    job_id = await queue.enqueue("test.bad", {}, account_id=acc)
    await worker.run_once()
    job = await queue.get(job_id)
    assert job is not None and job.status == "dead" and job.attempts == 1


async def test_unknown_kind_is_dead_with_clear_error(session_factory: SF, clock: FakeClock) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)
    worker = Worker(queue, {})
    job_id = await queue.enqueue("nobody.knows", {}, account_id=acc)
    await worker.run_once()
    job = await queue.get(job_id)
    assert job is not None and job.status == "dead"
    assert job.last_error is not None
    assert "неизвестный вид задачи" in job.last_error and "nobody.knows" in job.last_error


async def test_handler_crash_does_not_stop_worker(session_factory: SF, clock: FakeClock) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)
    done: list[str] = []

    async def crash(job: Job) -> None:
        raise ZeroDivisionError

    async def ok(job: Job) -> None:
        done.append(job.kind)

    worker = Worker(queue, {"a.crash": HandlerSpec(crash), "b.ok": HandlerSpec(ok)})
    await queue.enqueue("a.crash", {}, account_id=acc)
    clock.advance(1)
    second = await queue.enqueue("b.ok", {}, account_id=acc)
    assert await worker.drain() == 2
    assert done == ["b.ok"]
    job = await queue.get(second)
    assert job is not None and job.status == "done"


async def test_job_runs_in_account_context(session_factory: SF, clock: FakeClock) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)
    ctx: dict[str, object] = {}

    async def handle(job: Job) -> None:
        ctx.update(structlog.contextvars.get_contextvars())

    worker = Worker(queue, {"test.ctx": HandlerSpec(handle)})
    job_id = await queue.enqueue("test.ctx", {}, account_id=acc)
    await worker.run_once()
    assert ctx["account_id"] == str(acc)
    assert ctx["job_id"] == str(job_id)
    assert ctx["job_kind"] == "test.ctx"
    assert "account_id" not in structlog.contextvars.get_contextvars()  # контекст снят


async def test_stop_waits_for_current_job(session_factory: SF, clock: FakeClock) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(job: Job) -> None:
        started.set()
        await release.wait()

    worker = Worker(queue, {"test.slow": HandlerSpec(slow)}, poll_interval=0.01)
    job_id = await queue.enqueue("test.slow", {}, account_id=acc)
    second = await queue.enqueue("test.slow", {}, account_id=acc)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(started.wait(), timeout=5)
    stop.set()
    await asyncio.sleep(0.05)
    assert not task.done()  # текущая задача не брошена
    release.set()
    await asyncio.wait_for(task, timeout=5)
    job = await queue.get(job_id)
    assert job is not None and job.status == "done"
    untouched = await queue.get(second)
    assert untouched is not None and untouched.status == "queued"  # новую не взял


async def test_run_loop_picks_up_new_jobs_and_releases_stale(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await _account(session_factory)
    queue = _queue(session_factory, clock)
    stale = await queue.enqueue("test.ok", {"stale": True}, account_id=acc)
    (claimed,) = await queue.claim("dead-worker", 1)  # воркер умер, задача зависла
    assert claimed.id == stale
    clock.advance(timedelta(hours=1).total_seconds())
    done = asyncio.Event()
    seen: list[UUID] = []

    async def handle(job: Job) -> None:
        seen.append(job.id)
        if len(seen) == 2:
            done.set()

    worker = Worker(
        queue,
        {"test.ok": HandlerSpec(handle)},
        poll_interval=0.01,
        stale_after=timedelta(minutes=30),
        stale_check_every=1,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    fresh = await queue.enqueue("test.ok", {}, account_id=acc)
    await asyncio.wait_for(done.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert set(seen) == {stale, fresh}
