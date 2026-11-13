"""Очередь задач в БД."""

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue


@pytest.fixture
def queue(session_factory: async_sessionmaker[AsyncSession], clock: FakeClock) -> JobQueue:
    return JobQueue(
        lambda: UnitOfWork(session_factory),
        clock,
        max_attempts=3,
        base_delay=timedelta(seconds=10),
        max_delay=timedelta(hours=1),
    )


async def _account(session_factory: async_sessionmaker[AsyncSession], handle: str) -> UUID:
    async with UnitOfWork(session_factory) as uow:
        acc = await AccountsRepo(uow.session).create(handle=handle)
        return acc.id


@pytest.fixture
async def account_id(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    return await _account(session_factory, "jobs_a")


async def test_job_claim_is_exclusive(queue: JobQueue, account_id: UUID) -> None:
    ids = [await queue.enqueue("test.job", {"n": i}, account_id=account_id) for i in range(20)]
    batches = await asyncio.gather(*(queue.claim(f"w{i}", limit=10) for i in range(4)))
    claimed = [job.id for batch in batches for job in batch]
    assert len(claimed) == len(set(claimed)) == 20
    assert set(claimed) == set(ids)
    assert all(job.status == "running" for batch in batches for job in batch)

    await queue.enqueue("test.one", {}, account_id=account_id)
    first, second = await asyncio.gather(queue.claim("a", 1), queue.claim("b", 1))
    assert sorted([len(first), len(second)]) == [0, 1]


async def test_job_idempotency_key_dedupes(
    queue: JobQueue, account_id: UUID, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    j1 = await queue.enqueue("x.sync", {"a": 1}, account_id=account_id, idempotency_key="k1")
    j2 = await queue.enqueue("x.sync", {"a": 2}, account_id=account_id, idempotency_key="k1")
    assert j1 == j2
    job = await queue.get(j1)
    assert job is not None and job.payload == {"a": 1}
    other = await _account(session_factory, "jobs_b")
    j3 = await queue.enqueue("x.sync", {"a": 1}, account_id=other, idempotency_key="k1")
    assert j3 != j1
    j4 = await queue.enqueue("x.sync", {"a": 1}, account_id=account_id)
    j5 = await queue.enqueue("x.sync", {"a": 1}, account_id=account_id)
    assert j4 != j5
    assert len(await queue.claim("w", limit=10)) == 4


async def test_job_retry_backoff_then_dead(
    queue: JobQueue, clock: FakeClock, account_id: UUID
) -> None:
    job_id = await queue.enqueue("test.flaky", {}, account_id=account_id)
    delays: list[timedelta] = []
    for attempt in (1, 2):
        [job] = await queue.claim("w", limit=1)
        assert job.id == job_id and job.attempts == attempt and job.locked_by == "w"
        await queue.fail(job_id, f"boom {attempt}")
        state = await queue.get(job_id)
        assert state is not None and state.status == "queued" and state.locked_by is None
        assert state.last_error == f"boom {attempt}"
        delays.append(state.run_at - clock.now())
        assert await queue.claim("w", limit=1) == []
        clock.advance(delays[-1].total_seconds())
    assert delays[0] >= timedelta(seconds=10)
    assert delays[1] > delays[0]
    [job] = await queue.claim("w", limit=1)
    assert job.attempts == 3
    await queue.fail(job_id, "boom 3")
    state = await queue.get(job_id)
    assert state is not None and state.status == "dead" and state.last_error == "boom 3"
    clock.advance(86_400)
    assert await queue.claim("w", limit=10) == []


async def test_job_run_at_respected(queue: JobQueue, clock: FakeClock, account_id: UUID) -> None:
    job_id = await queue.enqueue(
        "test.later", {}, account_id=account_id, run_at=clock.now() + timedelta(hours=1)
    )
    assert await queue.claim("w", limit=10) == []
    clock.advance(3599)
    assert await queue.claim("w", limit=10) == []
    clock.advance(1)
    [job] = await queue.claim("w", limit=10)
    assert job.id == job_id


async def test_stale_lock_released(queue: JobQueue, clock: FakeClock, account_id: UUID) -> None:
    stale = await queue.enqueue("test.stale", {}, account_id=account_id)
    [job] = await queue.claim("w1", limit=1)
    assert job.id == stale
    clock.advance(600)
    fresh = await queue.enqueue("test.fresh", {}, account_id=account_id)
    [job] = await queue.claim("w2", limit=1)
    assert job.id == fresh
    assert await queue.release_stale(older_than=timedelta(minutes=5)) == 1
    state = await queue.get(stale)
    assert state is not None and state.status == "queued" and state.locked_by is None
    [job] = await queue.claim("w3", limit=1)
    assert job.id == stale
    await queue.complete(stale)
    done = await queue.get(stale)
    assert done is not None and done.status == "done"
    assert await queue.claim("w4", limit=10) == []
