import asyncio
import os
import signal

import pytest

from xcopilot import main as main_module
from xcopilot.logging import bind_request_id


async def test_serve_returns_when_stopped() -> None:
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(main_module.serve(stop), timeout=1)


def test_run_exits_with_code_2_on_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    with pytest.raises(SystemExit) as exc:
        main_module.run()
    assert exc.value.code == 2
    assert "BOT_TOKEN" in capsys.readouterr().err


def test_run_logs_service_started(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:t")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    async def fake_main() -> None:
        return None

    monkeypatch.setattr(main_module, "_main", fake_main)
    main_module.run()
    out = capsys.readouterr().out
    assert "service_started" in out
    assert "service_stopped" in out
    assert "123:t" not in out


def test_bind_request_id_generates_when_missing() -> None:
    assert len(bind_request_id()) == 32
    assert bind_request_id("abc") == "abc"


async def test_main_stops_on_sigterm() -> None:
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(main_module._main(), timeout=2)
