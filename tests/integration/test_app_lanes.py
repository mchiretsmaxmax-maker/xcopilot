"""Аудит E (AUD-007): приложение поднимает две полосы воркера и один раз возвращает задачи."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fakes import FakeTelegram
from xcopilot.app import SLOW_KINDS, build_app, serve_app
from xcopilot.bot.deps import BotDeps
from xcopilot.config import Settings
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.handlers import KNOWN_KINDS
from xcopilot.metrics.events import clear_listeners
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, WRITER_TOPICS_KIND


@pytest.fixture(autouse=True)
def _no_listeners() -> Iterator[None]:
    yield
    clear_listeners()


def _settings(db_url: str, tmp_path: Path) -> Settings:
    return Settings(
        bot_token="123456:FAKE-TOKEN-for-tests",
        database_url=db_url,
        owner_tg_ids=[100],
        data_dir=tmp_path,
    )


def test_slow_kinds_are_writer_kinds() -> None:
    expected = {
        WRITER_GENERATE_KIND,
        WRITER_TOPICS_KIND,
        "quality.gate",
        "brain.extract",
        "learning.propose",
        "reports.daily",
    }
    assert set(SLOW_KINDS) == expected
    assert SLOW_KINDS <= KNOWN_KINDS


async def test_build_app_has_fast_and_slow_lanes(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(_settings(db_url, tmp_path), bot=telegram.bot)
    try:
        fast, slow = app.workers
        assert app.worker is fast
        assert fast.worker_id.endswith("-fast") and slow.worker_id.endswith("-slow")
        assert fast.host_prefix == slow.host_prefix == f"{socket.gethostname()}:"
        assert fast.exclude_kinds == SLOW_KINDS and fast.kinds is None
        assert slow.kinds == SLOW_KINDS and slow.exclude_kinds is None
        assert fast.registry is slow.registry is app.registry
    finally:
        await app.aclose()


async def test_serve_app_recovers_previous_process_jobs_once(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(
        _settings(db_url, tmp_path), bot=telegram.bot, worker_poll=0.01, scheduler_interval=0.01
    )
    async with UnitOfWork(app.session_factory) as uow:
        glob = await AccountsRepo(uow.session).get_global()
    job_id = await app.queue.enqueue("db.ensure_partitions", {}, account_id=glob.id)
    # прошлая жизнь процесса на этом хосте взяла задачу в быстрой полосе и упала
    [taken] = await app.queue.claim(f"{socket.gethostname()}:1-fast", 1)
    assert taken.id == job_id
    recovered: list[int] = []
    original = app.worker.recover

    async def counting() -> int:
        n = await original()
        recovered.append(n)
        return n

    for w in app.workers:
        w.recover = counting  # type: ignore[method-assign]
    polled = asyncio.Event()

    async def fake_poll(deps: BotDeps, stop: asyncio.Event) -> None:
        polled.set()
        await stop.wait()

    stop = asyncio.Event()
    task = asyncio.create_task(serve_app(app, stop, poll=fake_poll))
    await asyncio.wait_for(polled.wait(), timeout=10)
    for _ in range(500):
        job = await app.queue.get(job_id)
        if job is not None and job.status == "done":
            break
        await asyncio.sleep(0.01)
    assert job is not None and job.status == "done"
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert recovered == [1]  # один раз на процесс, а не по разу на полосу
