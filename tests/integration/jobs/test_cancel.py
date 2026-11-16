"""fix/bot-control: отмена задач — очередь, воркер, событие job.cancelled."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobCancelled, JobQueue
from xcopilot.jobs.worker import HandlerSpec, Worker

SF = async_sessionmaker[AsyncSession]


@pytest.fixture
def queue(session_factory: SF, clock: FakeClock) -> JobQueue:
    return JobQueue(lambda: UnitOfWork(session_factory), clock)


async def _account(sf: SF, handle: str) -> UUID:
    async with UnitOfWork(sf) as uow:
        return (await AccountsRepo(uow.session).create(handle=handle)).id


@pytest.fixture
async def acc(session_factory: SF) -> UUID:
    return await _account(session_factory, "cancel_a")


async def _scalar(sf: SF, sql: str, **params: object) -> object:
    async with sf() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def test_cancel_queued_is_immediate_and_not_claimed(queue: JobQueue, acc: UUID) -> None:
    job_id = await queue.enqueue("test.k", {}, account_id=acc)
    assert await queue.cancel(job_id, account_id=acc) == "cancelled"
    job = await queue.get(job_id)
    assert job is not None and job.status == "cancelled"
    assert await queue.is_cancelled(job_id) is True
    assert await queue.claim("w", 5) == []


async def test_cancel_is_idempotent_and_emits_one_event(
    queue: JobQueue, acc: UUID, session_factory: SF
) -> None:
    job_id = await queue.enqueue("test.k", {}, account_id=acc)
    assert await queue.cancel(job_id, account_id=acc) == "cancelled"
    assert await queue.cancel(job_id, account_id=acc) == "cancelled"
    n = await _scalar(session_factory, "SELECT count(*) FROM events WHERE type = 'job.cancelled'")
    assert n == 1


async def test_cancel_finished_job_changes_nothing(queue: JobQueue, acc: UUID) -> None:
    job_id = await queue.enqueue("test.k", {}, account_id=acc)
    [job] = await queue.claim("w", 1)
    await queue.complete(job.id)
    assert await queue.cancel(job_id, account_id=acc) == "done"
    assert await queue.cancel(UUID(int=1), account_id=acc) is None  # нет такой


async def test_cancel_other_account_job_is_refused(
    queue: JobQueue, acc: UUID, session_factory: SF
) -> None:
    other = await _account(session_factory, "cancel_b")
    job_id = await queue.enqueue("test.k", {}, account_id=other)
    assert await queue.cancel(job_id, account_id=acc) is None
    job = await queue.get(job_id)
    assert job is not None and job.status == "queued"


async def test_complete_and_fail_do_not_overwrite_cancelled(queue: JobQueue, acc: UUID) -> None:
    job_id = await queue.enqueue("test.k", {}, account_id=acc)
    [job] = await queue.claim("w", 1)
    assert await queue.cancel(job.id, account_id=acc) == "cancelled"  # running → cancelled
    await queue.complete(job.id)
    got = await queue.get(job_id)
    assert got is not None and got.status == "cancelled"
    assert await queue.fail(job.id, "boom") == "cancelled"
    got = await queue.get(job_id)
    assert got is not None and got.status == "cancelled"
    assert await queue.release(job.id) is False


async def test_cancel_all_and_by_progress_key(queue: JobQueue, acc: UUID) -> None:
    a = await queue.enqueue("writer.generate", {"progress": "k1"}, account_id=acc)
    b = await queue.enqueue("quality.gate", {"progress": "k1"}, account_id=acc)
    c = await queue.enqueue("writer.topics", {"progress": "k2"}, account_id=acc)
    d = await queue.enqueue("ingest.process", {"source_id": "abc"}, account_id=acc)
    assert sorted(await queue.cancel_progress(acc, "k1")) == sorted([a, b])
    assert await queue.cancel_progress(acc, "k1") == []  # повтор — ничего нового
    assert await queue.cancel_progress(acc, "source:abc") == [d]
    assert await queue.cancel_all(acc) == [c]
    assert await queue.cancel_all(acc) == []


async def test_worker_interrupts_running_job_when_cancelled(queue: JobQueue, acc: UUID) -> None:
    started = asyncio.Event()
    finished = False
    reported: list[UUID] = []

    async def slow(job: Job) -> None:
        nonlocal finished
        started.set()
        await asyncio.sleep(30)
        finished = True

    async def on_cancel(job: Job) -> None:
        reported.append(job.id)

    worker = Worker(queue, {"test.slow": HandlerSpec(slow)}, cancel_poll=0.02, on_cancel=on_cancel)
    job_id = await queue.enqueue("test.slow", {}, account_id=acc)
    run = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), 5)
    assert await queue.cancel(job_id, account_id=acc) == "cancelled"
    assert await asyncio.wait_for(run, 5) is True
    assert finished is False
    assert reported == [job_id]
    job = await queue.get(job_id)
    assert job is not None and job.status == "cancelled"


async def test_job_cancelled_exception_from_handler_ends_as_cancelled(
    queue: JobQueue, acc: UUID
) -> None:
    dead: list[Job] = []

    async def stops(job: Job) -> None:
        await queue.cancel(job.id, account_id=job.account_id)
        raise JobCancelled(job.id)

    async def on_dead(job: Job, exc: BaseException) -> None:
        dead.append(job)

    worker = Worker(queue, {"test.stop": HandlerSpec(stops)}, on_dead=on_dead)
    job_id = await queue.enqueue("test.stop", {}, account_id=acc)
    [job] = await queue.claim("w", 1)
    assert await worker.process(job) == "cancelled"
    assert dead == []
    got = await queue.get(job_id)
    assert got is not None and got.status == "cancelled" and got.attempts == 1


async def test_result_of_cancelled_job_is_discarded(queue: JobQueue, acc: UUID) -> None:
    """Обработчик успел закончить, но задачу отменили в конце: complete не перетирает."""

    async def finishes(job: Job) -> None:
        await queue.cancel(job.id, account_id=job.account_id)

    worker = Worker(queue, {"test.fin": HandlerSpec(finishes)}, cancel_poll=60)
    job_id = await queue.enqueue("test.fin", {}, account_id=acc)
    [job] = await queue.claim("w", 1)
    assert await worker.process(job) == "cancelled"
    got = await queue.get(job_id)
    assert got is not None and got.status == "cancelled"
