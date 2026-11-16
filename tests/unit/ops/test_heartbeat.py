"""Аудит I: внешний сторож — пульс раз в минуту, /fail при зависании циклов."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import respx
import structlog

from tests.fakes.clock import FakeClock
from xcopilot.config import Settings
from xcopilot.logging import register_secrets, setup_logging
from xcopilot.ops.heartbeat import Heartbeat

URL = "https://hc-ping.example/abc-secret-uuid"


@dataclass
class FakeLoop:
    last_tick_at: datetime | None = None
    busy_since: datetime | None = None


def _make(
    clock: FakeClock,
    http: httpx.AsyncClient,
    *,
    url: str = URL,
    sched: FakeLoop | None = None,
    workers: list[FakeLoop] | None = None,
) -> tuple[Heartbeat, FakeLoop, list[FakeLoop]]:
    sched = sched or FakeLoop(clock.now())
    workers = workers if workers is not None else [FakeLoop(clock.now()), FakeLoop(clock.now())]
    hb = Heartbeat(http, clock, url, scheduler=sched, workers=workers)
    return hb, sched, workers


async def test_heartbeat_disabled_without_url() -> None:
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock(assert_all_called=False) as mock:
        route = mock.route().respond(200)
        hb, _, _ = _make(clock, http, url="")
        assert not hb.enabled
        stop = asyncio.Event()
        await asyncio.wait_for(hb.run(stop), timeout=2)  # без адреса цикл сразу выходит
        assert route.call_count == 0


async def test_heartbeat_pings_when_loops_alive() -> None:
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        ok = mock.get(URL).respond(200)
        fail = mock.get(URL + "/fail").respond(200)
        hb, _, _ = _make(clock, http)
        clock.advance(120)
        for loop in (hb._scheduler, *hb._workers):  # noqa: SLF001
            loop.last_tick_at = clock.now()
        await hb.beat()
        assert ok.call_count == 1 and fail.call_count == 0
        assert hb.last_ok_at == clock.now()


async def test_heartbeat_reports_fail_when_scheduler_stalled() -> None:
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        ok = mock.get(URL).respond(200)
        fail = mock.get(URL + "/fail").respond(200)
        hb, sched, workers = _make(clock, http)
        clock.advance(6 * 60)
        for w in workers:
            w.last_tick_at = clock.now()  # воркеры живы, планировщик молчит 6 минут
        assert not hb.healthy()
        await hb.beat()
        assert fail.call_count == 1 and ok.call_count == 0


async def test_heartbeat_fails_when_any_worker_stalled() -> None:
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(URL).respond(200)
        fail = mock.get(URL + "/fail").respond(200)
        hb, sched, workers = _make(clock, http)
        clock.advance(6 * 60)
        sched.last_tick_at = clock.now()
        workers[0].last_tick_at = clock.now()  # медленная полоса не отмечается
        await hb.beat()
        assert fail.call_count == 1


async def test_heartbeat_tolerates_long_job() -> None:
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        ok = mock.get(URL).respond(200)
        fail = mock.get(URL + "/fail").respond(200)
        hb, sched, workers = _make(clock, http)
        workers[1].busy_since = clock.now()
        clock.advance(10 * 60)
        sched.last_tick_at = clock.now()
        workers[0].last_tick_at = clock.now()  # у второго тик старый, но он занят 10 минут
        await hb.beat()
        assert ok.call_count == 1 and fail.call_count == 0

        clock.advance(36 * 60)  # занят уже 46 минут
        sched.last_tick_at = clock.now()
        workers[0].last_tick_at = clock.now()
        await hb.beat()
        assert fail.call_count == 1


async def test_heartbeat_network_error_does_not_crash_loop() -> None:
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(URL).mock(side_effect=httpx.ConnectError("нет сети"))
        hb, sched, workers = _make(clock, http)
        with structlog.testing.capture_logs() as logs:
            await hb.beat()  # не бросает
        assert any(r["event"] == "heartbeat_failed" for r in logs)
        assert hb.last_ok_at is None


async def test_heartbeat_loop_survives_error_and_stops(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        route = mock.get(URL).mock(side_effect=httpx.ConnectError("нет сети"))
        sched = FakeLoop(clock.now())
        hb = Heartbeat(http, clock, URL, scheduler=sched, workers=[], interval=0.01)
        stop = asyncio.Event()
        task = asyncio.create_task(hb.run(stop))
        for _ in range(200):
            if route.call_count >= 2:
                break
            await asyncio.sleep(0.01)
        assert route.call_count >= 2 and not task.done()
        stop.set()
        await asyncio.wait_for(task, timeout=2)


async def test_heartbeat_url_masked_in_logs(capfd) -> None:  # type: ignore[no-untyped-def]
    s = Settings(bot_token="t", database_url="x", heartbeat_url=URL)  # type: ignore[call-arg]
    assert URL in s.secret_values()
    register_secrets(s.secret_values())
    setup_logging("INFO")
    clock = FakeClock()
    async with httpx.AsyncClient() as http, respx.mock() as mock:
        mock.get(URL).mock(side_effect=httpx.ConnectError(f"не достучаться до {URL}"))
        hb, _, _ = _make(clock, http)
        await hb.beat()
    out = capfd.readouterr().out
    assert "heartbeat_failed" in out
    assert "abc-secret-uuid" not in out and "***" in out
    json.loads(out.strip().splitlines()[-1])
