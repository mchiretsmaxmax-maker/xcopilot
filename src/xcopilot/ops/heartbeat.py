"""Внешний сторож: пульс на адрес службы-сторожа (аудит I, ADR 0030).

Раз в `interval` секунд процесс шлёт GET на `HEARTBEAT_URL`. Циклы здоровы, если планировщик
тикал не позже `STALL_AFTER` назад, а каждый воркер либо тикал так же недавно, либо занят одной
задачей не дольше `BUSY_LIMIT` (долгая задача писателя — не зависание). Здоров → GET `url`,
иначе GET `url + "/fail"`. Сбой сети — в лог, цикл жив. При штатной остановке ничего не шлётся:
служба заметит тишину сама (на перенос и обновление проверку ставят на паузу — runbook).
Адрес — секрет (`Settings.secret_values()`), в логах маскируется.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

import httpx
import structlog

from xcopilot.clock import Clock

log = structlog.get_logger()

STALL_AFTER = timedelta(minutes=5)
BUSY_LIMIT = timedelta(minutes=45)
REQUEST_TIMEOUT = 10.0


class Probe(Protocol):
    last_tick_at: datetime | None


class WorkerProbe(Probe, Protocol):
    busy_since: datetime | None


class Heartbeat:
    def __init__(
        self,
        http: httpx.AsyncClient,
        clock: Clock,
        url: str,
        *,
        scheduler: Probe,
        workers: Sequence[WorkerProbe],
        interval: float = 60.0,
    ) -> None:
        self._http = http
        self._clock = clock
        self._url = url
        self._scheduler = scheduler
        self._workers = list(workers)
        self.interval = interval
        self._started_at = clock.now()
        self.last_ok_at: datetime | None = None  # последний успешно доставленный «живой» пульс

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def _fresh(self, tick: datetime | None, now: datetime) -> bool:
        # до первого тика отсчёт от старта сторожа: циклы только запускаются
        return now - (tick or self._started_at) <= STALL_AFTER

    def healthy(self) -> bool:
        now = self._clock.now()
        if not self._fresh(self._scheduler.last_tick_at, now):
            return False
        for w in self._workers:
            if w.busy_since is not None:
                if now - w.busy_since > BUSY_LIMIT:
                    return False
            elif not self._fresh(w.last_tick_at, now):
                return False
        return True

    async def beat(self) -> None:
        """Один пульс: GET на адрес (здоров) или на `/fail`; сбой сети — только в лог."""
        ok = self.healthy()
        target = self._url if ok else f"{self._url}/fail"
        try:
            response = await self._http.get(target, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except Exception as exc:
            log.warning("heartbeat_failed", error=f"{type(exc).__name__}: {exc}", healthy=ok)
            return
        if ok:
            self.last_ok_at = self._clock.now()
        else:
            log.warning("heartbeat_fail_sent")

    async def run(self, stop: asyncio.Event) -> None:
        """Цикл до `stop`; без адреса сразу выходит."""
        if not self.enabled:
            return
        log.info("heartbeat_started", interval=self.interval)
        while not stop.is_set():
            await self.beat()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
        log.info("heartbeat_stopped")
