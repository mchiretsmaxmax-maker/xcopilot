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

from xcopilot.bot.progress import SOURCE_PREFIX
from xcopilot.clock import Clock
from xcopilot.db.partitions import PARTITIONS_JOB_KIND, ensure_all_partitions
from xcopilot.db.repos import AccountsRepo, SourcesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.ingest.service import INGEST_KIND, IngestService
from xcopilot.jobs.queue import Job, JobCancelled, JobQueue
from xcopilot.jobs.worker import HandlerSpec
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.rollup import account_tz, backfill, local_today, run_rollups

if TYPE_CHECKING:
    from xcopilot.analytics.kit import AnalyticsKit
    from xcopilot.bot.deps import BotDeps
    from xcopilot.brain.analyze import Analyzer
    from xcopilot.brain.harvest import Harvester
    from xcopilot.brain.history import HistoryImporter
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.ops.alerts import AlertDeps
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
        "brain.batch_summary",
        "brain.history_import",
        "brain.detect",
        "brain.extract",
        "brain.nightly",
        "brain.weekly_digest",
        "brain.harvest_replies",
        "brain.harvest_recheck",
        "writer.generate",
        "writer.topics",
        "x.recheck_metrics",
        "bot.notify_budget",
        "quality.gate",
        "cards.expire",
        "publications.match",
        "analytics.sync",
        "analytics.snapshot",
        "reports.daily",
        "learning.propose",
        "ops.alerts",
        "ops.cleanup",
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
    analytics: AnalyticsKit | None = None  # своя аналитика (этап 14)
    alerts: AlertDeps | None = None  # алерты владельцу (этап 15); None — без бюджета и шлюза
    harvester: Harvester | None = None  # сбор ответов (ADR 0034); None — задачи пропускаются


def build_registry(deps: JobDeps) -> dict[str, HandlerSpec]:
    """Все виды задач проекта одной таблицей; KNOWN_KINDS — те же ключи."""
    from xcopilot.analytics import jobs as analytics
    from xcopilot.analytics.sync import SNAPSHOT_KIND as ANALYTICS_SNAPSHOT_KIND
    from xcopilot.analytics.sync import SYNC_KIND as ANALYTICS_SYNC_KIND
    from xcopilot.bot.budget_events import BOT_NOTIFY_BUDGET_KIND, handle_budget_notification
    from xcopilot.brain import digest
    from xcopilot.brain import jobs as brain
    from xcopilot.learning import jobs as learning
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

    def progress_of() -> Any:
        return getattr(deps.bot, "progress", None)

    async def material_key(job: Job) -> str | None:
        """Статус разбора — только для материала, который прислал владелец (не для истории)."""
        board = progress_of()
        if board is None:
            return None
        sid = UUID(str(job.payload["source_id"]))
        key = f"{SOURCE_PREFIX}{sid}"
        async with uow() as u:
            src = await SourcesRepo(u.session, job.account_id).get(sid)
        if src is None or "accepted_by" not in (src.meta or {}):
            return None
        started: str | None = await board.begin(job.account_id, "material", key=key)
        return started

    async def ingest(job: Job) -> object:
        key = await material_key(job) if job.attempts <= 1 else None
        if key and progress_of():
            await progress_of().step(key, "read")
        await handle_ingest_process(job, service=deps.ingest)
        return None

    async def analyze(job: Job) -> object:
        key = f"{SOURCE_PREFIX}{job.payload['source_id']}"
        if progress_of():
            await progress_of().step(key, "analyze")
        analyzed = await brain.handle_brain_analyze(job, analyzer=deps.analyzer)
        if progress_of():
            await progress_of().done(key)
        if analyzed:  # этап 15: детекторы по свежему разбору (исход мог прийти раньше разбора)
            await deps.queue.enqueue(
                brain.DETECT_KIND,
                {"source_id": job.payload["source_id"]},
                account_id=job.account_id,
                idempotency_key=f"detect:{job.id}",
            )
        return analyzed

    async def outcome(job: Job) -> object:
        basis = await brain.handle_brain_outcome(job, uow_factory=uow, clock=clock)
        if basis not in ("deferred", "none"):  # этап 15: исход пришёл после разбора
            await deps.queue.enqueue(
                brain.DETECT_KIND,
                {"source_id": job.payload["source_id"]},
                account_id=job.account_id,
                idempotency_key=f"detect:{job.id}",
            )
        return basis

    async def batch_summary(job: Job) -> object:
        from xcopilot.brain.batch import handle_batch_summary

        return await handle_batch_summary(job, uow_factory=uow, notifier=deps.notifier)

    async def history(job: Job) -> object:
        return await brain.handle_brain_history_import(
            job, importer=deps.importer, notifier=deps.notifier
        )

    async def harvest_replies(job: Job) -> object:
        if deps.harvester is None:
            return 0
        return await brain.handle_harvest(job, harvester=deps.harvester)

    async def harvest_recheck(job: Job) -> object:
        if deps.harvester is None:
            return 0
        return await brain.handle_recheck(job, harvester=deps.harvester)

    async def detect(job: Job) -> object:
        return await brain.handle_detect(job, deps=deps.brain)

    async def extract(job: Job) -> object:
        return await brain.handle_extract(job, deps=deps.brain, notifier=deps.notifier)

    async def nightly(job: Job) -> object:
        return await brain.handle_nightly(job, deps=deps.brain)

    async def weekly_digest(job: Job) -> object:
        return await digest.handle_weekly_digest(job, deps=deps.brain, notifier=deps.notifier)

    async def generate(job: Job) -> object:
        # этап 12: варианты — в ворота качества, карточки — после их решения
        gate_queue = deps.queue if deps.quality is not None else None
        return await writer.handle_writer_generate(
            job,
            writer=deps.writer,
            notifier=deps.notifier,
            gate_queue=gate_queue,
            progress=progress_of(),
        )

    async def topics(job: Job) -> object:
        return await writer.handle_writer_topics(
            job, topics=deps.topics, notifier=deps.notifier, progress=progress_of()
        )

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
            if await deps.queue.is_cancelled(job.id):  # владелец отменил: карточки не показываем
                raise JobCancelled(job.id)
            ids = [UUID(d) for d in job.payload["draft_ids"]]
            await deps.review.presenter.show_for_drafts(
                job.account_id, ids, view=job.payload.get("view")
            )
        if progress_of() is not None:
            await progress_of().done(job.payload.get("progress"))
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

    def analytics_kit(kind: str) -> AnalyticsKit:
        if deps.analytics is None:
            raise RuntimeError(f"{kind}: аналитика не собрана (JobDeps.analytics)")
        return deps.analytics

    async def analytics_sync(job: Job) -> object:
        kit = analytics_kit(ANALYTICS_SYNC_KIND)
        return await analytics.handle_analytics_sync(job, sync=kit.sync)

    async def analytics_snapshot(job: Job) -> object:
        kit = analytics_kit(ANALYTICS_SNAPSHOT_KIND)
        return await analytics.handle_analytics_snapshot(job, sync=kit.sync)

    async def reports_daily(job: Job) -> object:
        kit = analytics_kit(analytics.REPORTS_DAILY_KIND)
        return await analytics.handle_reports_daily(
            job,
            reports=kit.reports,
            notifier=deps.notifier,
            uow_factory=uow,
            analyst=kit.analyst,
        )

    async def learning_propose(job: Job) -> object:
        return await learning.handle_learning_propose(job, deps=deps.brain, notifier=deps.notifier)

    async def ops_alerts(job: Job) -> object:
        from xcopilot.ops.alerts import AlertDeps, handle_ops_alerts

        alert_deps = deps.alerts or AlertDeps(uow_factory=uow, clock=deps.clock)
        return await handle_ops_alerts(job, deps=alert_deps, notifier=deps.notifier)

    async def ops_cleanup(job: Job) -> object:
        from xcopilot.ops.cleanup import handle_ops_cleanup

        return await handle_ops_cleanup(job, uow_factory=uow, clock=clock)

    writer_permanent = tuple(writer.PERMANENT)
    return {
        PARTITIONS_JOB_KIND: HandlerSpec(partitions),
        METRICS_ROLLUP_KIND: HandlerSpec(rollup),
        INGEST_KIND: HandlerSpec(ingest),
        brain.ANALYZE_KIND: HandlerSpec(analyze),
        brain.OUTCOME_KIND: HandlerSpec(outcome),
        "brain.batch_summary": HandlerSpec(batch_summary),
        brain.HISTORY_KIND: HandlerSpec(history, permanent=(XModeError, XAuthError)),
        brain.DETECT_KIND: HandlerSpec(detect),
        brain.EXTRACT_KIND: HandlerSpec(extract),
        brain.NIGHTLY_KIND: HandlerSpec(nightly),
        brain.HARVEST_KIND: HandlerSpec(harvest_replies),  # быстрая полоса: writer не зовёт
        brain.RECHECK_KIND: HandlerSpec(harvest_recheck),
        digest.WEEKLY_DIGEST_KIND: HandlerSpec(weekly_digest),
        writer.WRITER_GENERATE_KIND: HandlerSpec(generate, permanent=writer_permanent),
        writer.WRITER_TOPICS_KIND: HandlerSpec(topics, permanent=writer_permanent),
        RECHECK_KIND: HandlerSpec(recheck, permanent=(XModeError, XAuthError)),
        BOT_NOTIFY_BUDGET_KIND: HandlerSpec(budget),
        # неверный payload (не UUID) повтором не исправить; модели ворота обрабатывают сами
        quality.QUALITY_GATE_KIND: HandlerSpec(gate, permanent=(ValueError, KeyError)),
        review.CARDS_EXPIRE_KIND: HandlerSpec(cards_expire),
        review.PUBLICATIONS_MATCH_KIND: HandlerSpec(publications_match),
        ANALYTICS_SYNC_KIND: HandlerSpec(analytics_sync),
        ANALYTICS_SNAPSHOT_KIND: HandlerSpec(analytics_snapshot),
        analytics.REPORTS_DAILY_KIND: HandlerSpec(reports_daily),
        learning.LEARNING_PROPOSE_KIND: HandlerSpec(learning_propose),
        "ops.alerts": HandlerSpec(ops_alerts),
        "ops.cleanup": HandlerSpec(ops_cleanup),
    }
