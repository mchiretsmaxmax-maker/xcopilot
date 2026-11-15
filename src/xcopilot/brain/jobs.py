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

from xcopilot.brain import extract, lifecycle, merge, outcomes, snapshots
from xcopilot.brain.analyze import ANALYZE_KIND, Analyzer
from xcopilot.brain.config import BrainSettings, load_brain_config
from xcopilot.brain.context import BrainContext, BrainLLM, NoEmbedder, NoLLM
from xcopilot.brain.evidence import record_from_material
from xcopilot.brain.history import HISTORY_KIND, HistoryImporter
from xcopilot.brain.scoring import recompute
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.errors import NotFound
from xcopilot.jobs.queue import Job
from xcopilot.metrics.events import EventType, add_listener

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
    await JobsRepo(session).enqueue(
        account_id=account_id,
        kind=ANALYZE_KIND,
        payload={"source_id": source_id},
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
    """payload {"source_id"}: детекторы и доказательства (свой пост — own_outcome)."""
    source_id = UUID(str(job.payload["source_id"]))
    async with deps.uow_factory() as uow:
        ctx = deps.ctx(uow.session, job.account_id)
        written = await record_from_material(ctx, source_id)
        if written:
            await recompute(ctx)
        return written


async def handle_extract(job: Job, *, deps: BrainDeps) -> int:
    """Извлечение по всем новым пачкам аккаунта; вернуть число затронутых знаний."""
    async with deps.uow_factory() as uow:
        return len(await extract.run(deps.ctx(uow.session, job.account_id)))


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
