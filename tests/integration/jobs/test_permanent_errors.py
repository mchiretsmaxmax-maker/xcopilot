"""fix/bot-ux-progress: постоянная ошибка модели — сразу dead и сообщение владельцу."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.bot.failures import DeadJobReporter, failure_text
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec, Worker
from xcopilot.llm.errors import PermanentProviderError
from xcopilot.writer.jobs import WriterUnavailableError

SF = async_sessionmaker[AsyncSession]


class Inbox:
    def __init__(self) -> None:
        self.sent: list[tuple[UUID, str]] = []

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        self.sent.append((account_id, text))


async def _account(sf: SF) -> UUID:
    async with UnitOfWork(sf) as uow:
        return (await AccountsRepo(uow.session).create(handle="p", niche="x", lang="ru")).id


def _worker(sf: SF, clock: FakeClock, fn, inbox: Inbox, attempts: int = 5):  # type: ignore[no-untyped-def]
    queue = JobQueue(lambda: UnitOfWork(sf), clock, max_attempts=attempts)
    reporter = DeadJobReporter(inbox, None)
    worker = Worker(queue, {"writer.generate": HandlerSpec(fn)}, on_dead=reporter)
    return queue, worker


async def test_permanent_provider_error_is_dead_at_once_and_owner_told(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await _account(session_factory)
    inbox = Inbox()
    calls = 0

    async def boom(job: Job) -> None:
        nonlocal calls
        calls += 1
        raise PermanentProviderError("claude_cli", kind="cli_not_found")

    queue, worker = _worker(session_factory, clock, boom, inbox)
    job_id = await queue.enqueue("writer.generate", {}, account_id=acc)
    assert await worker.run_once() is True
    job = await queue.get(job_id)
    assert job is not None and job.status == "dead" and calls == 1
    assert inbox.sent == [
        (acc, "Писатель недоступен: Claude CLI не найден. Задача остановлена.")
    ]


async def test_transient_failure_retries_and_tells_only_after_last_attempt(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await _account(session_factory)
    inbox = Inbox()

    async def flaky(job: Job) -> None:
        raise WriterUnavailableError("лимит")

    queue, worker = _worker(session_factory, clock, flaky, inbox, attempts=2)
    await queue.enqueue("writer.generate", {}, account_id=acc)
    await worker.run_once()
    assert inbox.sent == []  # пока будет повтор — тихо
    clock.advance(queue.backoff(1).total_seconds() + 1)
    await worker.run_once()
    assert len(inbox.sent) == 1
    assert "не получилось" in inbox.sent[0][1]


def test_failure_text_is_human_per_kind() -> None:
    cli = PermanentProviderError("claude_cli", kind="cli_not_found")
    assert failure_text("writer.generate", cli).startswith("Писатель недоступен")
    assert failure_text("writer.topics", cli).startswith("Писатель недоступен")
    assert "ключ" in failure_text("quality.gate", PermanentProviderError("deepseek", status=401))
    assert "Разбор материала" in failure_text("brain.analyze", RuntimeError("x"))
