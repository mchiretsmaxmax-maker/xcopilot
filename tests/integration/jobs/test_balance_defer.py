"""fix/deepseek-balance: 402 (кончились деньги) — задача не умирает, ждёт и повторяется сама."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec, Worker
from xcopilot.llm.errors import status_error

SF = async_sessionmaker[AsyncSession]


async def _account(sf: SF) -> UUID:
    async with UnitOfWork(sf) as uow:
        return (await AccountsRepo(uow.session).create(handle="b", niche="x", lang="ru")).id


async def test_balance_exhausted_defers_without_spending_attempts(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await _account(session_factory)
    dead: list[Job] = []
    calls = 0

    async def handler(job: Job) -> None:
        nonlocal calls
        calls += 1
        if calls <= 7:  # больше, чем max_attempts: баланс пустой долго
            raise status_error("deepseek", 402)

    async def on_dead(job: Job, exc: BaseException) -> None:
        dead.append(job)

    queue = JobQueue(lambda: UnitOfWork(session_factory), clock, max_attempts=5)
    worker = Worker(
        queue,
        {"brain.analyze": HandlerSpec(handler)},
        on_dead=on_dead,
        balance_delay=timedelta(minutes=30),
    )
    job_id = await queue.enqueue("brain.analyze", {}, account_id=acc)
    for _ in range(7):
        assert await worker.run_once() is True
        job = await queue.get(job_id)
        assert job is not None and job.status == "queued"
        assert job.run_at == clock.now() + timedelta(minutes=30)
        assert job.attempts == 0
        assert await worker.run_once() is False  # раньше срока не берём
        clock.advance(30 * 60)
    assert await worker.run_once() is True  # баланс пополнили
    job = await queue.get(job_id)
    assert job is not None and job.status == "done"
    assert dead == []
