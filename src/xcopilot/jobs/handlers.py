"""Обработчики фоновых задач (вид задачи → функция).

`metrics.rollup`: payload пустой — пересчитать вчерашний день по поясу аккаунта;
`{"day": "YYYY-MM-DD"}` — указанный день; `{"date_from": …, "date_to": …}` — бэкфилл диапазона.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.ingest.service import IngestService
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.rollup import account_tz, backfill, local_today, run_rollups

if TYPE_CHECKING:
    from xcopilot.bot.deps import BotDeps
    from xcopilot.brain.analyze import Analyzer
    from xcopilot.brain.history import HistoryImporter
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.writer.drafts import WriterService
    from xcopilot.writer.topics import TopicService
    from xcopilot.x.client import XClient

METRICS_ROLLUP_KIND = "metrics.rollup"


class AppNotifier(Protocol):
    """Уведомления участникам аккаунта (bot.notify.Notifier)."""

    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...

    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


async def handle_metrics_rollup(
    job: Job, *, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> int:
    """Свёртка метрик аккаунта задачи; возвращает число записанных строк."""
    async with uow_factory() as uow:
        session = uow.session
        if await AccountsRepo(session).get(job.account_id) is None:
            raise NotFound(f"аккаунт {job.account_id} не найден")
        await sync_catalog(session)
        payload = job.payload
        if "date_from" in payload and "date_to" in payload:
            return await backfill(
                session,
                job.account_id,
                date.fromisoformat(payload["date_from"]),
                date.fromisoformat(payload["date_to"]),
                clock=clock,
            )
        if "day" in payload:
            day = date.fromisoformat(payload["day"])
        else:
            tz = await account_tz(session, job.account_id)
            day = local_today(clock.now(), tz) - timedelta(days=1)
        return await run_rollups(session, job.account_id, day, clock=clock)


async def handle_ingest_process(job: Job, *, service: IngestService) -> None:
    """Задача ingest.process: payload {"source_id": "<uuid>"} (так ставит и онбординг).

    Идемпотентна: обработанный источник не меняется. Временный сбой (X, модель эмбеддингов)
    пробрасывается — очередь повторит задачу с паузой.
    """
    await service.process(UUID(str(job.payload["source_id"])), account_id=job.account_id)


# --- реестр (заглушка red-фазы) ---
KNOWN_KINDS: frozenset[str] = frozenset()


@dataclass
class JobDeps:
    """Всё, что нужно обработчикам задач (собирает xcopilot.app или тестовый стенд)."""

    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    queue: JobQueue
    ingest: IngestService
    analyzer: Analyzer
    importer: HistoryImporter
    writer: WriterService
    topics: TopicService
    brain: BrainDeps
    x_for: Callable[[UUID], Awaitable[XClient]]
    notifier: AppNotifier
    bot: BotDeps


def build_registry(deps: JobDeps) -> dict[str, HandlerSpec]:
    raise NotImplementedError
