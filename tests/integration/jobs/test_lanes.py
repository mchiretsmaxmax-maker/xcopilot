"""Аудит E (AUD-007): две полосы воркера — медленная (писатель) и быстрая (всё остальное)."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec, Registry, Worker

SF = async_sessionmaker[AsyncSession]
SLOW = ("writer.generate",)
FAST_KIND = "ingest.process"
SLOW_KIND = "writer.generate"


@pytest.fixture
def queue(session_factory: SF, clock: FakeClock) -> JobQueue:
    return JobQueue(lambda: UnitOfWork(session_factory), clock)


@pytest.fixture
async def acc(session_factory: SF) -> UUID:
    async with UnitOfWork(session_factory) as uow:
        return (await AccountsRepo(uow.session).create(handle="lanes")).id


def _lanes(queue: JobQueue, registry: Registry, **kw: object) -> tuple[Worker, Worker]:
    fast = Worker(queue, registry, worker_id="h:7-fast", exclude_kinds=SLOW, **kw)  # type: ignore[arg-type]
    slow = Worker(queue, registry, worker_id="h:7-slow", kinds=SLOW, **kw)  # type: ignore[arg-type]
    return fast, slow


async def _status(queue: JobQueue, job_id: UUID) -> Job:
    job = await queue.get(job_id)
    assert job is not None
    return job


async def test_claim_respects_kind_filter(queue: JobQueue, acc: UUID) -> None:
    slow_id = await queue.enqueue(SLOW_KIND, {}, account_id=acc)
    fast_id = await queue.enqueue(FAST_KIND, {}, account_id=acc)
    taken = await queue.claim("w", 5, kinds=SLOW)
    assert [j.id for j in taken] == [slow_id]
    assert await queue.claim("w", 5, kinds=SLOW) == []
    taken = await queue.claim("w", 5, kinds=SLOW, exclude=True)
    assert [j.id for j in taken] == [fast_id]


async def test_unfiltered_claim_takes_every_kind(queue: JobQueue, acc: UUID) -> None:
    await queue.enqueue(SLOW_KIND, {}, account_id=acc)
    await queue.enqueue(FAST_KIND, {}, account_id=acc)
    assert len(await queue.claim("w", 5)) == 2


async def test_worker_ids_keep_host_prefix() -> None:
    fast = Worker(None, {}, worker_id="c0ffee:42-fast", exclude_kinds=SLOW)  # type: ignore[arg-type]
    slow = Worker(None, {}, worker_id="c0ffee:42-slow", kinds=SLOW)  # type: ignore[arg-type]
    assert fast.host_prefix == slow.host_prefix == "c0ffee:"


async def test_worker_rejects_both_kind_filters() -> None:
    with pytest.raises(ValueError):
        Worker(None, {}, kinds=SLOW, exclude_kinds=SLOW)  # type: ignore[arg-type]


async def test_fast_lane_runs_while_slow_lane_busy(queue: JobQueue, acc: UUID) -> None:
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    fast_done = asyncio.Event()

    async def writer(job: Job) -> None:
        slow_started.set()
        await release_slow.wait()

    async def ingest(job: Job) -> None:
        fast_done.set()

    registry = {SLOW_KIND: HandlerSpec(writer), FAST_KIND: HandlerSpec(ingest)}
    fast, slow = _lanes(queue, registry, poll_interval=0.01)
    slow_id = await queue.enqueue(SLOW_KIND, {}, account_id=acc)
    stop = asyncio.Event()
    slow_task = asyncio.create_task(slow.run(stop, recover=False))
    fast_task = asyncio.create_task(fast.run(stop, recover=False))
    try:
        await asyncio.wait_for(slow_started.wait(), 5)
        fast_id = await queue.enqueue(FAST_KIND, {}, account_id=acc)
        await asyncio.wait_for(fast_done.wait(), 5)  # писатель всё ещё занят
        for _ in range(200):
            if (await _status(queue, fast_id)).status == "done":
                break
            await asyncio.sleep(0.01)
        assert (await _status(queue, fast_id)).status == "done"
        assert (await _status(queue, slow_id)).status == "running"
        release_slow.set()
        for _ in range(200):
            if (await _status(queue, slow_id)).status == "done":
                break
            await asyncio.sleep(0.01)
        assert (await _status(queue, slow_id)).status == "done"
    finally:
        stop.set()
        await asyncio.wait_for(asyncio.gather(slow_task, fast_task), 5)


async def test_two_lanes_shutdown_releases_both_jobs(queue: JobQueue, acc: UUID) -> None:
    started = {SLOW_KIND: asyncio.Event(), FAST_KIND: asyncio.Event()}

    async def hangs(job: Job) -> None:
        started[job.kind].set()
        await asyncio.Event().wait()

    registry = {SLOW_KIND: HandlerSpec(hangs), FAST_KIND: HandlerSpec(hangs)}
    fast, slow = _lanes(queue, registry, poll_interval=0.01, shutdown_grace=0.05)
    slow_id = await queue.enqueue(SLOW_KIND, {}, account_id=acc)
    fast_id = await queue.enqueue(FAST_KIND, {}, account_id=acc)
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(slow.run(stop, recover=False)),
        asyncio.create_task(fast.run(stop, recover=False)),
    ]
    await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started.values())), 5)
    stop.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    for job_id in (slow_id, fast_id):
        job = await _status(queue, job_id)
        assert job.status == "queued" and job.locked_by is None
        assert job.attempts == 0  # попытка не засчитана


async def test_recover_releases_jobs_of_both_lanes(queue: JobQueue, acc: UUID) -> None:
    slow_id = await queue.enqueue(SLOW_KIND, {}, account_id=acc)
    fast_id = await queue.enqueue(FAST_KIND, {}, account_id=acc)
    # прошлый процесс этого хоста взял по задаче в каждой полосе и упал
    await queue.claim("h:6-slow", 1, kinds=SLOW)
    await queue.claim("h:6-fast", 1, kinds=SLOW, exclude=True)
    fast, _slow = _lanes(queue, {})
    assert await fast.recover() == 2
    for job_id in (slow_id, fast_id):
        job = await _status(queue, job_id)
        assert job.status == "queued" and job.locked_by is None


async def test_lane_started_without_recover_keeps_other_lane_job(
    queue: JobQueue, acc: UUID
) -> None:
    """Полоса не возвращает в очередь задачу, которую уже взяла соседняя полоса того же хоста."""
    slow_id = await queue.enqueue(SLOW_KIND, {}, account_id=acc)
    await queue.claim("h:7-slow", 1, kinds=SLOW)  # соседняя полоса уже работает
    fast, _slow = _lanes(queue, {}, poll_interval=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(fast.run(stop, recover=False))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    job = await _status(queue, slow_id)
    assert job.status == "running" and job.locked_by == "h:7-slow"


async def test_unknown_kind_goes_to_fast_lane_and_dies(queue: JobQueue, acc: UUID) -> None:
    job_id = await queue.enqueue("nobody.knows", {}, account_id=acc)
    fast, slow = _lanes(queue, {})
    assert await slow.run_once() is False
    assert await fast.run_once() is True
    job = await _status(queue, job_id)
    assert job.status == "dead" and "неизвестный вид задачи" in (job.last_error or "")


@pytest.mark.parametrize("kind", [SLOW_KIND, FAST_KIND])
async def test_cancel_interrupts_running_job_in_each_lane(
    queue: JobQueue, acc: UUID, kind: str
) -> None:
    started = asyncio.Event()
    finished = False
    reported: list[UUID] = []

    async def long(job: Job) -> None:
        nonlocal finished
        started.set()
        await asyncio.sleep(30)
        finished = True

    async def on_cancel(job: Job) -> None:
        reported.append(job.id)

    registry = {SLOW_KIND: HandlerSpec(long), FAST_KIND: HandlerSpec(long)}
    fast, slow = _lanes(queue, registry, poll_interval=0.01, cancel_poll=0.02, on_cancel=on_cancel)
    job_id = await queue.enqueue(kind, {}, account_id=acc)
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(slow.run(stop, recover=False)),
        asyncio.create_task(fast.run(stop, recover=False)),
    ]
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert (await _status(queue, job_id)).locked_by == (
            "h:7-slow" if kind == SLOW_KIND else "h:7-fast"
        )
        assert await queue.cancel(job_id, account_id=acc) == "cancelled"
        for _ in range(200):
            if reported:
                break
            await asyncio.sleep(0.01)
        assert reported == [job_id]
        assert finished is False
        assert (await _status(queue, job_id)).status == "cancelled"
    finally:
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 5)
