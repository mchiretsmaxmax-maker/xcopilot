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
from xcopilot.db.partitions import PARTITIONS_JOB_KIND, ensure_all_partitions
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.ingest.service import INGEST_KIND, IngestService
from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.jobs.worker import HandlerSpec
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.rollup import account_tz, backfill, local_today, run_rollups

if TYPE_CHECKING:
    from xcopilot.bot.deps import BotDeps
    from xcopilot.brain.analyze import Analyzer
    from xcopilot.brain.history import HistoryImporter
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.quality.loop import QualityGate
    from xcopilot.review.kit import ReviewKit
    from xcopilot.writer.drafts import WriterService
    from xcopilot.writer.topics import TopicService
    from xcopilot.x.client import XClient

METRICS_ROLLUP_KIND = "metrics.rollup"
KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "db.ensure_partitions",
        METRICS_ROLLUP_KIND,
        "ingest.process",
        "brain.analyze",
        "brain.outcome",
        "brain.history_import",
        "brain.detect",
        "brain.extract",
        "brain.nightly",
        "writer.generate",
        "writer.topics",
        "x.recheck_metrics",
        "bot.notify_budget",
        "quality.gate",
        "cards.expire",
        "publications.match",
    }
)


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


# --- реестр: вид задачи → обработчик (ADR 0022) ------------------------------------------


async def handle_ensure_partitions(
    job: Job, *, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> int:
    """Задача db.ensure_partitions: месячные партиции журналов на 2 месяца вперёд."""
    async with uow_factory() as uow:
        created = await ensure_all_partitions(uow.session, today=clock.now().date())
    return sum(len(names) for names in created.values())


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
    quality: QualityGate | None = None  # ворота качества (этап 11); None — задача упадёт
    review: ReviewKit | None = None  # карточки и публикация (этап 12)


def build_registry(deps: JobDeps) -> dict[str, HandlerSpec]:
    """Все виды задач проекта одной таблицей; KNOWN_KINDS — те же ключи."""
    from xcopilot.bot.budget_events import BOT_NOTIFY_BUDGET_KIND, handle_budget_notification
    from xcopilot.brain import jobs as brain
    from xcopilot.quality import jobs as quality
    from xcopilot.review import jobs as review
    from xcopilot.writer import jobs as writer
    from xcopilot.x.client import RECHECK_KIND
    from xcopilot.x.errors import XAuthError, XModeError
    from xcopilot.x.recheck import handle_recheck_metrics

    uow, clock = deps.uow_factory, deps.clock

    async def partitions(job: Job) -> object:
        return await handle_ensure_partitions(job, uow_factory=uow, clock=clock)

    async def rollup(job: Job) -> object:
        return await handle_metrics_rollup(job, uow_factory=uow, clock=clock)

    async def ingest(job: Job) -> object:
        await handle_ingest_process(job, service=deps.ingest)
        return None

    async def analyze(job: Job) -> object:
        return await brain.handle_brain_analyze(job, analyzer=deps.analyzer)

    async def outcome(job: Job) -> object:
        return await brain.handle_brain_outcome(job, uow_factory=uow, clock=clock)

    async def history(job: Job) -> object:
        return await brain.handle_brain_history_import(
            job, importer=deps.importer, notifier=deps.notifier
        )

    async def detect(job: Job) -> object:
        return await brain.handle_detect(job, deps=deps.brain)

    async def extract(job: Job) -> object:
        return await brain.handle_extract(job, deps=deps.brain)

    async def nightly(job: Job) -> object:
        return await brain.handle_nightly(job, deps=deps.brain)

    async def generate(job: Job) -> object:
        # этап 12: варианты — в ворота качества, карточки — после их решения
        gate_queue = deps.queue if deps.quality is not None else None
        return await writer.handle_writer_generate(
            job, writer=deps.writer, notifier=deps.notifier, gate_queue=gate_queue
        )

    async def topics(job: Job) -> object:
        return await writer.handle_writer_topics(job, topics=deps.topics, notifier=deps.notifier)

    async def recheck(job: Job) -> object:
        client = await deps.x_for(job.account_id)
        return await handle_recheck_metrics(
            job, client_for=lambda _: client, uow_factory=uow, clock=clock
        )

    async def budget(job: Job) -> object:
        return await handle_budget_notification(job, deps=deps.bot)

    async def gate(job: Job) -> object:
        if deps.quality is None:
            raise RuntimeError("quality.gate: ворота качества не собраны (JobDeps.quality)")
        decisions = await quality.handle_quality_gate(job, gate=deps.quality)
        if deps.review is not None:  # этап 12: карточки только после решения ворот
            ids = [UUID(d) for d in job.payload["draft_ids"]]
            await deps.review.presenter.show_for_drafts(job.account_id, ids)
        return decisions

    async def cards_expire(job: Job) -> object:
        if deps.review is None:
            raise RuntimeError("cards.expire: карточки не собраны (JobDeps.review)")
        return await review.handle_cards_expire(job, presenter=deps.review.presenter)

    async def publications_match(job: Job) -> object:
        if deps.review is None:
            raise RuntimeError("publications.match: карточки не собраны (JobDeps.review)")
        return await review.handle_publications_match(
            job, matcher=deps.review.matcher, x_for=deps.x_for, notifier=deps.notifier
        )

    writer_permanent = tuple(writer.PERMANENT)
    return {
        PARTITIONS_JOB_KIND: HandlerSpec(partitions),
        METRICS_ROLLUP_KIND: HandlerSpec(rollup),
        INGEST_KIND: HandlerSpec(ingest),
        brain.ANALYZE_KIND: HandlerSpec(analyze),
        brain.OUTCOME_KIND: HandlerSpec(outcome),
        brain.HISTORY_KIND: HandlerSpec(history, permanent=(XModeError, XAuthError)),
        brain.DETECT_KIND: HandlerSpec(detect),
        brain.EXTRACT_KIND: HandlerSpec(extract),
        brain.NIGHTLY_KIND: HandlerSpec(nightly),
        writer.WRITER_GENERATE_KIND: HandlerSpec(generate, permanent=writer_permanent),
        writer.WRITER_TOPICS_KIND: HandlerSpec(topics, permanent=writer_permanent),
        RECHECK_KIND: HandlerSpec(recheck, permanent=(XModeError, XAuthError)),
        BOT_NOTIFY_BUDGET_KIND: HandlerSpec(budget),
        # неверный payload (не UUID) повтором не исправить; модели ворота обрабатывают сами
        quality.QUALITY_GATE_KIND: HandlerSpec(gate, permanent=(ValueError, KeyError)),
        review.CARDS_EXPIRE_KIND: HandlerSpec(cards_expire),
        review.PUBLICATIONS_MATCH_KIND: HandlerSpec(publications_match),
    }
