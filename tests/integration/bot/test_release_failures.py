"""Этап 15: отказы — рестарт посреди задачи, корректная остановка, провайдеры и БД недоступны."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.cards_stand import make_cards_stand
from tests.fakes import FakeClock, FakeTelegram
from tests.integration.bot.conftest import make_onboarded
from tests.quality_stand import good
from tests.writer_stand import DISTINCT, draft_reply, post_task
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.db.engine import create_engine, session_factory
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec, Worker
from xcopilot.llm.errors import ProviderUnavailable
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, generate_payload

SF = async_sessionmaker[AsyncSession]


async def test_restart_mid_generation_resumes_job(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    st = await make_cards_stand(session_factory, telegram, deps)
    st.gs.ws.writer.push(draft_reply(*DISTINCT))
    st.gs.judge.push(good(), good(), good())
    job_id = await st.queue.enqueue(
        WRITER_GENERATE_KIND, generate_payload(post_task("газ на l2")), account_id=st.account_id
    )
    # старый процесс взял задачу и упал посреди генерации: блокировка осталась
    [taken] = await st.queue.claim("c0ffee:1", 1)
    assert taken.id == job_id

    # compose перезапустил тот же контейнер: тот же hostname и pid → тот же worker_id
    worker = Worker(st.queue, st.worker.registry, worker_id="c0ffee:1")
    assert await worker.recover() == 1  # не ждём 30 минут до «просроченной блокировки»
    await worker.drain()

    job = await st.queue.get(job_id)
    assert job is not None and job.status == "done"
    assert await st.count("generation_runs", "account_id = :a", a=st.account_id) == 1
    assert len(st.cards()) == 1


async def test_graceful_shutdown_releases_job_locks(
    session_factory: SF, deps: BotDeps, clock: FakeClock
) -> None:
    acc = await make_onboarded(session_factory)
    started = asyncio.Event()

    async def hangs(job: Job) -> None:
        started.set()
        await asyncio.Event().wait()  # задача длиннее, чем даёт остановка

    queue = deps.queue
    registry = {"test.hang": HandlerSpec(hangs)}

    async def check_released(job_id: UUID) -> None:
        job = await queue.get(job_id)
        assert job is not None
        assert job.status == "queued" and job.locked_by is None
        assert job.run_at <= clock.now()  # берётся сразу после рестарта, без паузы

    # SIGTERM: stop → текущей задаче даётся shutdown_grace, потом блокировка снимается
    job_id = await queue.enqueue("test.hang", {}, account_id=acc)
    worker = Worker(queue, registry, worker_id="w:1", poll_interval=0.01, shutdown_grace=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(started.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    await check_released(job_id)

    # жёсткая отмена цикла (TaskGroup гасит воркер) — блокировка тоже снимается
    started.clear()
    task = asyncio.create_task(worker.run(asyncio.Event()))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await check_released(job_id)


async def test_all_providers_down_bot_still_responds(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    st = await make_cards_stand(session_factory, telegram, deps)
    down = [ProviderUnavailable("provider down") for _ in range(20)]
    st.gs.ws.writer.push(*down)
    st.gs.ws.cheap.push(*down)
    owner = st.owner
    await owner.press_data("menu:write")
    await owner.press("Пост")
    await owner.say("почему комиссии в l2 падают")
    assert owner.last.text == texts.WRITING
    await st.drain()
    assert texts.WRITER_UNAVAILABLE in [m.text for m in telegram.sent]
    [status] = [r[0] for r in await st.rows("SELECT status FROM jobs WHERE kind = :k",
                                            k=WRITER_GENERATE_KIND)]  # fmt: skip
    assert status == "queued"  # повторится, когда модель вернётся; не dead

    for command in ("/help", "/cost", "/start"):
        replies = await owner.say(command)
        assert replies and replies[-1].text, command


async def test_db_reconnect_after_restart(db_url: str, postgres_url: str, clock: FakeClock) -> None:
    engine = create_engine(db_url)
    admin = create_async_engine(postgres_url)
    try:
        sf = session_factory(engine)

        def uow() -> UnitOfWork:
            return UnitOfWork(sf)

        queue = JobQueue(uow, clock)
        async with uow() as u:
            acc = (await u.session.execute(text("SELECT id FROM accounts LIMIT 1"))).scalar_one()
        await queue.enqueue("test.ok", {}, account_id=acc)
        # PostgreSQL перезапустился: все соединения пула оборваны
        async with admin.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE application_name = 'xcopilot' AND pid <> pg_backend_pid()"
                )
            )
        done: list[str] = []

        async def ok(job: Job) -> None:
            done.append(job.kind)

        worker = Worker(queue, {"test.ok": HandlerSpec(ok)}, worker_id="w:db")
        assert await worker.drain() == 1
        assert done == ["test.ok"]
    finally:
        await engine.dispose()
        await admin.dispose()
