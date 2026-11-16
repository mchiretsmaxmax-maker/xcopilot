"""Планировщик: по таблице `schedules` ставит периодические задачи в очередь (ADR 0022).

Раз в `interval` секунд (по умолчанию минута):
1. каждому рабочему аккаунту заводятся стандартные расписания `DEFAULT_SCHEDULES`
   (ON CONFLICT DO NOTHING — владелец может выключить или сдвинуть своё);
2. для каждого включённого расписания считается, наступил ли запуск по поясу аккаунта
   (`due_at`); наступил — задача ставится с ключом идемпотентности
   `sched:<kind>:<локальная дата>` и в той же транзакции пишется `last_run_at`.
Пропущенный запуск (процесс лежал) догоняется один раз; повтор тика не ставит дубль
даже при потерянном `last_run_at` — его держит ключ идемпотентности.

Частые задачи (`INTERVAL_SCHEDULES`, этап 15: `ops.alerts` каждые 15 минут) не живут в
`schedules`: каждому рабочему аккаунту на каждый интервал ставится одна задача с ключом
`every:<kind>:<начало интервала>`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from xcopilot.clock import Clock
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.repos.schedules import SchedulesRepo
from xcopilot.db.uow import UnitOfWork

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class DefaultSchedule:
    kind: str
    at_local_time: time
    weekday: int | None = None


# Ночные проходы по каждому аккаунту: свёртка метрик за вчера, затем «Мозг» (спад, переходы,
# снимок дня). db.ensure_partitions засеян миграцией у системного аккаунта.
DEFAULT_SCHEDULES: tuple[DefaultSchedule, ...] = (
    DefaultSchedule("metrics.rollup", time(0, 30)),
    DefaultSchedule("brain.nightly", time(3, 30)),
    # 9C: недельная сводка «чему научилась база» — понедельник, 09:00 по поясу аккаунта
    DefaultSchedule("brain.weekly_digest", time(9, 0), weekday=0),
    DefaultSchedule("cards.expire", time(4, 0)),  # этап 12: карточки старше 48 ч
    DefaultSchedule("publications.match", time(21, 0)),  # этап 12: сверка с лентой раз в день
    DefaultSchedule("analytics.sync", time(6, 0)),  # этап 14: свои посты и метрики
    DefaultSchedule("reports.daily", time(8, 0)),  # этап 14: отчёт за вчера, местное время
    # этап 13: проверка «5 новых ревью или неделя»; сам проход — только если пора
    DefaultSchedule("learning.propose", time(5, 30)),
)


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    kind: str
    every: timedelta


# Этап 15: проверка алертов владельцу (дедупликация «раз в час» — внутри ops.alerts).
INTERVAL_SCHEDULES: tuple[IntervalSchedule, ...] = (
    IntervalSchedule("ops.alerts", timedelta(minutes=15)),
)


def slot_start(now: datetime, every: timedelta) -> datetime:
    """Начало интервала длиной every, в который попадает now (от полуночи UTC 1970)."""
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    slots = (now - epoch) // every
    return epoch + every * slots


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def due_at(
    now: datetime, tz: str, at_local_time: time, weekday: int | None, last_run_at: datetime | None
) -> datetime | None:
    """Момент (UTC) сегодняшнего запуска по поясу tz, если он наступил и ещё не выполнен."""
    zone = _zone(tz)
    local_now = now.astimezone(zone)
    if weekday is not None and local_now.weekday() != weekday:
        return None
    occurrence = datetime.combine(local_now.date(), at_local_time, tzinfo=zone).astimezone(UTC)
    if now < occurrence:
        return None
    if last_run_at is not None and last_run_at >= occurrence:
        return None
    return occurrence


class Scheduler:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        interval: float = 60.0,
        max_attempts: int = 5,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self.interval = interval
        self.max_attempts = max_attempts
        self.last_tick_at: datetime | None = None  # аудит I: отметка для внешнего сторожа

    async def tick(self) -> list[UUID]:
        """Один проход: стандартные расписания + постановка наступивших; вернуть id задач."""
        now = self._clock.now()
        async with self._uow() as uow:
            repo = SchedulesRepo(uow.session)
            for d in DEFAULT_SCHEDULES:
                await repo.ensure_for_accounts(d.kind, d.at_local_time, d.weekday)
            rows = await repo.enabled()
        enqueued: list[UUID] = []
        for row in rows:
            occurrence = due_at(now, row.tz, row.at_local_time, row.weekday, row.last_run_at)
            if occurrence is None:
                continue
            local_day = occurrence.astimezone(_zone(row.tz)).date()
            async with self._uow() as uow:
                job_id = await JobsRepo(uow.session).enqueue(
                    account_id=row.account_id,
                    kind=row.kind,
                    payload=row.payload,
                    run_at=now,
                    max_attempts=self.max_attempts,
                    idempotency_key=f"sched:{row.kind}:{local_day.isoformat()}",
                )
                await SchedulesRepo(uow.session).mark_run(row.id, now)
            log.info("schedule_enqueued", kind=row.kind, account_id=str(row.account_id))
            enqueued.append(job_id)
        enqueued += await self._interval_tick(now)
        return enqueued

    async def _interval_tick(self, now: datetime) -> list[UUID]:
        if not INTERVAL_SCHEDULES:
            return []
        enqueued: list[UUID] = []
        async with self._uow() as uow:
            accounts = await SchedulesRepo(uow.session).working_accounts()
            jobs = JobsRepo(uow.session)
            for item in INTERVAL_SCHEDULES:
                slot = slot_start(now, item.every).isoformat()
                for account_id in accounts:
                    enqueued.append(
                        await jobs.enqueue(
                            account_id=account_id,
                            kind=item.kind,
                            payload={},
                            run_at=now,
                            max_attempts=1,  # следующий интервал сам проверит снова
                            idempotency_key=f"every:{item.kind}:{slot}",
                        )
                    )
        return enqueued

    async def run(self, stop: asyncio.Event) -> None:
        """Тикать до `stop`; сбой тика (БД недоступна) — в лог, следующий тик по расписанию."""
        log.info("scheduler_started")
        while not stop.is_set():
            self.last_tick_at = self._clock.now()
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
        log.info("scheduler_stopped")


__all__ = [
    "DEFAULT_SCHEDULES",
    "INTERVAL_SCHEDULES",
    "DefaultSchedule",
    "IntervalSchedule",
    "Scheduler",
    "due_at",
    "slot_start",
    "timedelta",
]
