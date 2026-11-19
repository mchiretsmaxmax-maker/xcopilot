"""Фоновые задачи «Мозга».

Этап 7A (разбор): brain.analyze, brain.outcome, brain.history_import.
Этап 9A (знания): brain.detect, brain.extract, brain.nightly — в конце файла.

Подписчик `emit` на `source.ingested` ставит `brain.analyze` в той же транзакции, что и
событие: принятый материал получает разбор в течение минуты (ТЗ v3.1, цель этапа 7A).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from xcopilot.brain import extract, harvest, lifecycle, merge, outcomes, snapshots
from xcopilot.brain.analyze import ANALYZE_KIND, Analyzer
from xcopilot.brain.config import BrainSettings, load_brain_config
from xcopilot.brain.context import BrainContext, BrainLLM, NoEmbedder, NoLLM
from xcopilot.brain.evidence import record_from_material, revise_material
from xcopilot.brain.history import HISTORY_KIND, HistoryImporter
from xcopilot.brain.scoring import recompute
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.errors import NotFound
from xcopilot.jobs.queue import Job
from xcopilot.metrics.events import EventType, add_listener
from xcopilot.style.auto import request_auto_rebuild

OUTCOME_KIND = outcomes.OUTCOME_KIND
__all__ = ["ANALYZE_KIND", "HISTORY_KIND", "OUTCOME_KIND"]
_NOW = datetime(1970, 1, 1, tzinfo=UTC)  # «сразу»: воркер берёт задачи с run_at ≤ его времени


class OwnerNotifier(Protocol):
    async def notify(self, account_id: UUID, text: str, keyboard: Any = None) -> int: ...


async def enqueue_analysis(
    session: DbSession, account_id: UUID, event_type: str, props: dict[str, Any]
) -> None:
    """Подписчик emit: принятый (проиндексированный) материал → задача brain.analyze."""
    if event_type != EventType.SOURCE_INGESTED.value:
        return
    source_id = str(props["source_id"])
    payload: dict[str, Any] = {"source_id": source_id}
    if props.get("silent"):
        payload["silent"] = True  # сбор ответов: сбой разбора владельцу не сообщается
    await JobsRepo(session).enqueue(
        account_id=account_id,
        kind=ANALYZE_KIND,
        payload=payload,
        run_at=_NOW,
        max_attempts=5,
        idempotency_key=f"{ANALYZE_KIND}:{source_id}",
    )


def install_analysis_on_ingest() -> None:
    """Подписаться на приём материалов (один раз при сборке приложения)."""
    add_listener(enqueue_analysis)


async def handle_brain_analyze(job: Job, *, analyzer: Analyzer) -> bool:
    """Задача brain.analyze: payload {"source_id"}; не готовый к разбору материал — пропуск."""
    result = await analyzer.analyze(job.account_id, UUID(str(job.payload["source_id"])))
    return result is not None


async def handle_brain_outcome(
    job: Job, *, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> str:
    """Задача brain.outcome (отложенный исход): пересчитать исход материала; вернуть basis."""
    async with uow_factory() as uow:
        out = await outcomes.compute_outcome(
            uow.session, job.account_id, UUID(str(job.payload["source_id"])), now=clock.now()
        )
    return "deferred" if out.deferred else out.basis


async def handle_brain_history_import(
    job: Job, *, importer: HistoryImporter, notifier: OwnerNotifier | None = None
) -> int:
    """Задача brain.history_import: payload {"limit"?}; итог — сообщением владельцу."""
    limit = int(job.payload.get("limit", 500))
    result = await importer.import_own_history(job.account_id, limit=limit)
    if notifier is not None:
        await notifier.notify(job.account_id, result.text)
    return result.imported


# ---------------------------------------------------------------- этап 9A: знания
DETECT_KIND = "brain.detect"
EXTRACT_KIND = "brain.extract"
NIGHTLY_KIND = "brain.nightly"


@dataclass
class BrainDeps:
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    llm: BrainLLM = field(default_factory=NoLLM)
    embedder: Embedder = field(default_factory=NoEmbedder)
    config: BrainSettings = field(default_factory=load_brain_config)

    def ctx(self, session: DbSession, account_id: UUID) -> BrainContext:
        return BrainContext(
            session=session,
            account_id=account_id,
            clock=self.clock,
            llm=self.llm,
            embedder=self.embedder,
            config=self.config,
        )


async def handle_detect(job: Job, *, deps: BrainDeps) -> int:
    """payload {"source_id", "revise"?}: детекторы и доказательства (свой пост — own_outcome).

    revise — исход или отношение изменились: прежние доказательства материала отзываются.
    """
    source_id = UUID(str(job.payload["source_id"]))
    async with deps.uow_factory() as uow:
        ctx = deps.ctx(uow.session, job.account_id)
        if job.payload.get("revise"):
            written = await revise_material(ctx, source_id)
        else:
            written = await record_from_material(ctx, source_id)
            if written:
                await recompute(ctx)
        await enqueue_extract(uow.session, job.account_id)  # после детекта — извлечение знаний
        source = await SourcesRepo(uow.session, job.account_id).get(source_id)
        if source is not None and source.stance == "own" and source.removed_at is None:
            # «это моё» (приём, «Всё моё» через revise) → проверка автопересборки паспорта
            await request_auto_rebuild(uow.session, job.account_id, now=deps.clock.now())
        return written


async def handle_extract(
    job: Job, *, deps: BrainDeps, notifier: OwnerNotifier | None = None
) -> int:
    """Извлечение по всем новым пачкам аккаунта; payload {"force"?} — ручной запуск.

    Вернуть число затронутых знаний; итог — сообщением владельцу (см. `extraction_text`).
    """
    force = bool(job.payload.get("force"))
    async with deps.uow_factory() as uow:
        report = await extract.run_report(deps.ctx(uow.session, job.account_id), force=force)
    text = extraction_text(report, force=force)
    if notifier is not None and text is not None:
        await notifier.notify(job.account_id, text)
    return len(report.touched)


async def handle_nightly(job: Job, *, deps: BrainDeps) -> int:
    """Пересчёт со спадом, переходы, разбор конфликтов, снимок дня; вернуть число переходов."""
    async with deps.uow_factory() as uow:
        account = await AccountsRepo(uow.session).get(job.account_id)
        if account is None:
            raise NotFound(f"аккаунт {job.account_id} не найден")
        ctx = deps.ctx(uow.session, job.account_id)
        now = deps.clock.now()
        await recompute(ctx, now=now)
        done = await lifecycle.run(ctx, now)
        if await merge.resolve_conflicts(ctx):
            await recompute(ctx, now=now)
        day = now.astimezone(ZoneInfo(account.tz or "UTC")).date()
        await snapshots.take(ctx, day)
        return len(done)


async def enqueue_extract(
    session: DbSession, account_id: UUID, *, force: bool = False
) -> UUID | None:
    """Поставить brain.extract; уже стоит в очереди — None (одна queued на аккаунт)."""
    repo = JobsRepo(session)
    if await repo.has_queued(account_id, EXTRACT_KIND):
        return None
    return await repo.enqueue(
        account_id=account_id,
        kind=EXTRACT_KIND,
        payload={"force": True} if force else {},
        run_at=_NOW,
        max_attempts=3,
        idempotency_key=None,
    )


def extraction_text(report: extract.ExtractionReport, *, force: bool) -> str | None:
    """Сообщение владельцу об итоге; None — молчать."""
    if report.added or report.updated:
        head = (
            f"🧠 Выучила из {report.pending} разборов: добавлено {len(report.added)}, "
            f"обновлено {len(report.updated)}."
        )
        lines = [f"• {kind}: «{statement}»" for kind, statement in report.examples]
        return "\n".join([head, *lines])
    if not force:
        return None
    if report.pending == 0:
        return "Новых знаний нет: все разборы уже обработаны."
    return (
        f"Новых знаний нет, мало материала: на {report.pending} разборах не нашлось устойчивых "
        "закономерностей (нужно минимум два похожих примера)."
    )


# ---------------------------------------------------------------- ADR 0034: сбор ответов
HARVEST_KIND = harvest.HARVEST_KIND
RECHECK_KIND = harvest.RECHECK_KIND


async def enqueue_harvest(
    session: DbSession, account_id: UUID, payload: dict[str, Any], *, now: datetime
) -> UUID | None:
    """Поставить сбор под постом сразу (ключ по post_id: второй раз — та же задача).

    Выключен `reply_learning` в настройках аккаунта — None, задача не ставится.
    """
    account = await AccountsRepo(session).get(account_id)
    if account is None or not harvest.reply_learning_on(account.settings):
        return None
    return await harvest.enqueue_harvest_job(session, account_id, payload, run_at=now)


async def handle_harvest(job: Job, *, harvester: harvest.Harvester) -> int:
    """brain.harvest_replies: payload из `harvest_payload`; вернуть число новых материалов."""
    return (await harvester.run(job.account_id, dict(job.payload))).stored


async def handle_recheck(job: Job, *, harvester: harvest.Harvester) -> int:
    """brain.harvest_recheck: перепроверка через сутки; вернуть число обновлённых ответов."""
    return (await harvester.recheck(job.account_id, dict(job.payload))).refreshed
