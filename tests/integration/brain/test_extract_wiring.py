"""fix/brain-extract-wiring: постановка brain.extract, ручной запуск, сообщение об итоге."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_core_stand import FakeBrainLLM, candidate_judge, make_ctx, new_account
from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.ingest_stand import RecordingNotifier
from tests.integration.brain.extract_helpers import candidate, count, result
from tests.integration.brain.test_extract import materials
from xcopilot.brain import extract
from xcopilot.brain.jobs import BrainDeps, enqueue_extract, handle_detect, handle_extract
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job
from xcopilot.jobs.scheduler import INTERVAL_SCHEDULES, Scheduler

SF = async_sessionmaker[AsyncSession]


def _job(acc: UUID, kind: str = "brain.extract", **payload: object) -> Job:
    return Job(
        id=uuid4(),
        account_id=acc,
        kind=kind,
        payload=dict(payload),
        status="running",
        attempts=1,
        max_attempts=5,
        run_at=datetime.now(UTC),
        locked_by=None,
        last_error=None,
    )


def _deps(sf: SF, llm: FakeBrainLLM) -> BrainDeps:
    return BrainDeps(
        uow_factory=lambda: UnitOfWork(sf),
        clock=FakeClock(datetime.now(UTC)),
        llm=llm,
        embedder=FakeEmbedder(),
    )


async def _queued(sf: SF, acc: UUID, kind: str = "brain.extract") -> int:
    async with sf() as s:
        return await count(
            s,
            "SELECT count(*) FROM jobs WHERE account_id=:a AND kind=:k AND status='queued'",
            a=acc,
            k=kind,
        )


async def test_force_takes_fewer_than_ten(session: AsyncSession) -> None:
    acc = await new_account(session)
    await materials(session, acc, 3)
    ctx = make_ctx(session, acc, clock=FakeClock(datetime.now(UTC)))
    assert await extract.build_batches(ctx) == []  # правило пачек не менялось
    forced = await extract.build_batches(ctx, force=True)
    assert [len(b.materials) for b in forced] == [3]
    await session.rollback()


async def test_force_takes_single_material(session: AsyncSession) -> None:
    acc = await new_account(session)
    await materials(session, acc, 1)
    ctx = make_ctx(session, acc, clock=FakeClock(datetime.now(UTC)))
    assert [len(b.materials) for b in await extract.build_batches(ctx, force=True)] == [1]
    await session.rollback()


async def test_manual_run_on_13_gives_knowledge_and_repeat_skips(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        await materials(uow.session, acc, 13)
    llm = FakeBrainLLM(judge=candidate_judge()).push(result(candidate()))
    notifier = RecordingNotifier()
    deps = _deps(session_factory, llm)

    assert await handle_extract(_job(acc, force=True), deps=deps, notifier=notifier) == 1
    assert len(notifier.sent) == 1
    msg = notifier.sent[0][1]
    assert "хук" in msg and "Личная цифра в первой строке" in msg and "добавлено 1" in msg
    async with session_factory() as s:
        assert (
            await count(
                s,
                "SELECT count(*) FROM events WHERE account_id=:a AND type='brain.extraction_done'",
                a=acc,
            )
            == 1
        )

    # повтор: те же разборы не берутся, модель не зовётся, владельцу — причина
    assert await handle_extract(_job(acc, force=True), deps=deps, notifier=notifier) == 0
    assert len(llm.gen_calls) == 1
    assert len(notifier.sent) == 2
    assert "Новых знаний нет" in notifier.sent[1][1] and "обработ" in notifier.sent[1][1]


async def test_auto_run_without_news_is_silent(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        await materials(uow.session, acc, 12)
    llm = FakeBrainLLM(judge=candidate_judge()).push(result())
    notifier = RecordingNotifier()
    await handle_extract(_job(acc), deps=_deps(session_factory, llm), notifier=notifier)
    assert len(llm.gen_calls) == 1 and notifier.sent == []


async def test_manual_run_without_news_explains(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        await materials(uow.session, acc, 2)
    llm = FakeBrainLLM(judge=candidate_judge()).push(result())
    notifier = RecordingNotifier()
    await handle_extract(_job(acc, force=True), deps=_deps(session_factory, llm), notifier=notifier)
    assert len(notifier.sent) == 1
    assert "Новых знаний нет, мало материала" in notifier.sent[0][1]


async def test_enqueue_extract_one_queued_per_account(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        other = await new_account(uow.session)
        first = await enqueue_extract(uow.session, acc)
        assert first is not None
        assert await enqueue_extract(uow.session, acc) is None
        assert await enqueue_extract(uow.session, acc, force=True) is None
        assert await enqueue_extract(uow.session, other) is not None
    assert await _queued(session_factory, acc) == 1


async def test_force_flag_goes_into_payload(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        await enqueue_extract(uow.session, acc, force=True)
    async with session_factory() as s:
        row = (
            await s.execute(
                text("SELECT payload FROM jobs WHERE account_id=:a AND kind='brain.extract'"),
                {"a": acc},
            )
        ).scalar_one()
    assert row == {"force": True}


async def test_detect_enqueues_extract_once(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        sources = await materials(uow.session, acc, 2)
    deps = _deps(session_factory, FakeBrainLLM())
    for sid in sources:
        await handle_detect(_job(acc, "brain.detect", source_id=str(sid)), deps=deps)
    assert await _queued(session_factory, acc) == 1


async def test_scheduler_enqueues_extract_hourly(session_factory: SF) -> None:
    assert any(
        i.kind == "brain.extract" and i.every.total_seconds() == 3600 for i in INTERVAL_SCHEDULES
    )
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
    clock = FakeClock(datetime(2026, 3, 10, 12, 5, tzinfo=UTC))
    sched = Scheduler(lambda: UnitOfWork(session_factory), clock)
    await sched.tick()
    await sched.tick()  # тот же час — без дубля
    assert await _queued(session_factory, acc) == 1
