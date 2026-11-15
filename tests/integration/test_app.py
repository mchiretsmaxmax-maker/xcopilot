"""Сборка живого приложения: зависимости из настроек, старт, бот + воркер + планировщик."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from xcopilot import main as main_module
from xcopilot.app import App, build_app, serve_app
from xcopilot.bot.deps import BotDeps
from xcopilot.brain.provider import BrainKnowledge
from xcopilot.config import Settings
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.handlers import KNOWN_KINDS
from xcopilot.metrics.events import clear_listeners

SF = async_sessionmaker[AsyncSession]


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


async def _scalar(app: App, sql: str) -> object:
    async with app.uow_factory() as uow:
        return (await uow.session.execute(text(sql))).scalar_one()


async def test_build_app_wires_services(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(_settings(db_url, tmp_path), bot=telegram.bot)
    try:
        assert set(app.registry) == KNOWN_KINDS
        assert app.bot_deps.ingest is not None
        assert app.bot_deps.ingest_batch is not None
        assert app.bot_deps.style is not None
        assert isinstance(app.bot_deps.knowledge, BrainKnowledge)
        await app.startup()
        assert int(str(await _scalar(app, "SELECT count(*) FROM prompts"))) > 0
        month = datetime.now(UTC).strftime("%Y%m")
        assert await _scalar(app, f"SELECT to_regclass('events_p{month}') IS NOT NULL") is True
    finally:
        await app.aclose()


async def test_x_for_builds_service_in_account_mode(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(_settings(db_url, tmp_path), bot=telegram.bot)
    try:
        async with app.uow_factory() as uow:
            acc = await AccountsRepo(uow.session).create(handle="xmode", x_read_mode="lookup")
            acc_id: UUID = acc.id
        x = await app.x_for(acc_id)
        assert getattr(x, "mode", None) == "lookup"
    finally:
        await app.aclose()


async def test_serve_app_runs_bot_worker_scheduler_and_stops(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(
        _settings(db_url, tmp_path), bot=telegram.bot, worker_poll=0.01, scheduler_interval=0.01
    )
    polled = asyncio.Event()

    async def fake_poll(deps: BotDeps, stop: asyncio.Event) -> None:
        polled.set()
        await stop.wait()

    stop = asyncio.Event()
    task = asyncio.create_task(serve_app(app, stop, poll=fake_poll))
    await asyncio.wait_for(polled.wait(), timeout=10)
    async with UnitOfWork(app.session_factory) as uow:
        glob = await AccountsRepo(uow.session).get_global()
    job_id = await app.queue.enqueue("db.ensure_partitions", {}, account_id=glob.id)
    for _ in range(500):
        job = await app.queue.get(job_id)
        if job is not None and job.status == "done":
            break
        await asyncio.sleep(0.01)
    assert job is not None and job.status == "done"
    stop.set()
    await asyncio.wait_for(task, timeout=10)


async def test_main_serve_builds_and_serves(
    monkeypatch: pytest.MonkeyPatch, db_url: str, tmp_path: Path
) -> None:
    calls: list[str] = []

    class FakeApp:
        pass

    def fake_build(settings: Settings) -> FakeApp:
        calls.append("build")
        return FakeApp()

    async def fake_serve(app: FakeApp, stop: asyncio.Event) -> None:
        calls.append("serve")

    monkeypatch.setattr(main_module, "build_app", fake_build)
    monkeypatch.setattr(main_module, "serve_app", fake_serve)
    await main_module.serve(asyncio.Event(), _settings(db_url, tmp_path))
    assert calls == ["build", "serve"]
