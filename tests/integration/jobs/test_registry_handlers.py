"""Обработчики реестра через воркер: партиции, свёртка, бюджет, перепроверка X, писатель."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram, FakeX
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.brain.jobs import BrainDeps
from xcopilot.config import Settings
from xcopilot.db.repos import AccountsRepo, UsersRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.handlers import JobDeps, build_registry
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import Worker
from xcopilot.llm.errors import ProviderUnavailable
from xcopilot.writer.drafts import WriterInputError
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, WRITER_TOPICS_KIND

SF = async_sessionmaker[AsyncSession]
OWNER = 100


@dataclass
class FailingTopics:
    calls: int = 0

    async def suggest_topics(self, account_id: UUID, n: int = 5) -> list[Any]:
        self.calls += 1
        raise ProviderUnavailable("claude_cli: лимит подписки")


@dataclass
class BadWriter:
    calls: list[str] = field(default_factory=list)

    async def generate(self, account_id: UUID, task: Any, **_: Any) -> Any:
        raise WriterInputError("ответ без целевого поста")

    async def generate_tail(self, account_id: UUID, ref: Any, *_: Any, **__: Any) -> Any:
        raise WriterInputError("нет поста")


@dataclass
class Rig:
    sf: SF
    clock: FakeClock
    queue: JobQueue
    worker: Worker
    account_id: UUID
    telegram: FakeTelegram
    x: FakeX

    async def run(self, kind: str, payload: dict[str, Any], account_id: UUID | None = None) -> Job:
        job_id = await self.queue.enqueue(kind, payload, account_id=account_id or self.account_id)
        await self.worker.run_once()
        job = await self.queue.get(job_id)
        assert job is not None
        return job


async def _rig(sf: SF, clock: FakeClock, telegram: FakeTelegram) -> Rig:
    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    async with uow() as u:
        user, _ = await UsersRepo(u.session).get_or_create(OWNER, is_admin=True)
        accounts = AccountsRepo(u.session)
        acc = await accounts.create(handle="reg", niche="Крипта", lang="ru", tz="UTC")
        await accounts.add_member(acc.id, user.id, role="owner")
        account_id = acc.id
    queue = JobQueue(uow, clock)
    settings = Settings(
        bot_token="123456:FAKE-TOKEN-for-tests",
        database_url="postgresql+asyncpg://unused",
        owner_tg_ids=[OWNER],
    )
    bot = BotDeps(bot=telegram.bot, settings=settings, uow_factory=uow, clock=clock, queue=queue)
    x = FakeX()

    async def x_for(_: UUID) -> Any:
        return x

    unused = cast(Any, object())
    deps = JobDeps(
        uow_factory=uow,
        clock=clock,
        queue=queue,
        ingest=unused,
        analyzer=unused,
        importer=unused,
        writer=cast(Any, BadWriter()),
        topics=cast(Any, FailingTopics()),
        brain=BrainDeps(uow_factory=uow, clock=clock),
        x_for=x_for,
        notifier=Notifier(telegram.bot, uow),
        bot=bot,
    )
    worker = Worker(queue, build_registry(deps), worker_id="reg")
    return Rig(sf, clock, queue, worker, account_id, telegram, x)


async def test_ensure_partitions_job_creates_future_months(
    session_factory: SF, telegram: FakeTelegram
) -> None:
    clock = FakeClock(datetime(2031, 5, 10, tzinfo=UTC))
    rig = await _rig(session_factory, clock, telegram)
    async with UnitOfWork(session_factory) as uow:
        glob = await AccountsRepo(uow.session).get_global()
    job = await rig.run("db.ensure_partitions", {}, account_id=glob.id)
    assert job.status == "done"
    async with session_factory() as s:
        made = await s.execute(text("SELECT to_regclass('events_p203107') IS NOT NULL"))
        assert made.scalar_one() is True


async def test_metrics_rollup_job_runs(session_factory: SF, telegram: FakeTelegram) -> None:
    rig = await _rig(session_factory, FakeClock(datetime(2026, 3, 11, 5, tzinfo=UTC)), telegram)
    job = await rig.run("metrics.rollup", {"day": "2026-03-10"})
    assert job.status == "done"


async def test_budget_notification_job_sends_message(
    session_factory: SF, clock: FakeClock, telegram: FakeTelegram
) -> None:
    rig = await _rig(session_factory, clock, telegram)
    props = {
        "scope": "llm",
        "period": "day",
        "spent_micro_usd": 4_000_000,
        "limit_micro_usd": 5_000_000,
    }
    job = await rig.run("bot.notify_budget", {"event_type": "budget.warning", "props": props})
    assert job.status == "done"
    assert len(telegram.texts_to(OWNER)) == 1


async def test_recheck_missing_post_is_dead_without_retries(
    session_factory: SF, clock: FakeClock, telegram: FakeTelegram
) -> None:
    rig = await _rig(session_factory, clock, telegram)
    job = await rig.run("x.recheck_metrics", {"post_id": "404", "handle": "alice"})
    assert job.status == "dead" and job.attempts == 1
    assert rig.x.calls == ["get_post:404"]


async def test_writer_unavailable_is_retried_and_owner_told(
    session_factory: SF, clock: FakeClock, telegram: FakeTelegram
) -> None:
    rig = await _rig(session_factory, clock, telegram)
    job = await rig.run(WRITER_TOPICS_KIND, {"n": 5})
    assert job.status == "queued" and job.run_at > clock.now()
    assert telegram.texts_to(OWNER) == [texts.WRITER_UNAVAILABLE]


async def test_writer_bad_task_is_dead(
    session_factory: SF, clock: FakeClock, telegram: FakeTelegram
) -> None:
    rig = await _rig(session_factory, clock, telegram)
    payload = {"mode": "trend_tail", "post_ref": {"text": "чужой пост"}, "own_topic": None}
    job = await rig.run(WRITER_GENERATE_KIND, payload)
    assert job.status == "dead" and job.attempts == 1
