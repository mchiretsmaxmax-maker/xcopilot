"""Сбой одного цикла останавливает процесс с ошибкой, а не молча (fix/worker-app-wiring)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fakes import FakeTelegram
from xcopilot import main as main_module
from xcopilot.app import ServiceFailed, build_app, serve_app
from xcopilot.bot.deps import BotDeps
from xcopilot.config import Settings
from xcopilot.metrics.events import clear_listeners


@pytest.fixture(autouse=True)
def _no_listeners() -> Iterator[None]:
    yield
    clear_listeners()


def _settings(db_url: str, tmp_path: Path) -> Settings:
    return Settings(bot_token="123456:FAKE-TOKEN-for-tests", database_url=db_url, data_dir=tmp_path)


async def test_crashed_bot_stops_worker_and_scheduler(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(_settings(db_url, tmp_path), bot=telegram.bot, worker_poll=0.01)

    async def crash(deps: BotDeps, stop: asyncio.Event) -> None:
        raise RuntimeError("Unauthorized")

    stop = asyncio.Event()
    with pytest.raises(ServiceFailed, match="bot"):
        await asyncio.wait_for(serve_app(app, stop, poll=crash), timeout=10)
    assert stop.is_set()


async def test_poller_that_returns_early_is_a_failure(
    db_url: str, tmp_path: Path, telegram: FakeTelegram
) -> None:
    app = build_app(_settings(db_url, tmp_path), bot=telegram.bot, worker_poll=0.01)

    async def quits(deps: BotDeps, stop: asyncio.Event) -> None:
        return None

    with pytest.raises(ServiceFailed, match="bot"):
        await asyncio.wait_for(serve_app(app, asyncio.Event(), poll=quits), timeout=10)


def test_run_exits_with_code_1_when_service_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:t")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    async def failing_main() -> None:
        raise ServiceFailed("bot")

    monkeypatch.setattr(main_module, "_main", failing_main)
    with pytest.raises(SystemExit) as exc:
        main_module.run()
    assert exc.value.code == 1
    assert "Сервис остановлен из-за ошибки: bot" in capsys.readouterr().err
    assert main_module._settings is None
