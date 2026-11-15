"""Фоновые задачи разбора (этап 7A): brain.analyze, brain.outcome, brain.history_import.

Подписчик `emit` на `source.ingested` ставит `brain.analyze` в той же транзакции, что и
событие: принятый материал получает разбор в течение минуты (ТЗ v3.1, цель этапа 7A).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from xcopilot.brain import outcomes
from xcopilot.brain.analyze import ANALYZE_KIND, Analyzer
from xcopilot.brain.history import HISTORY_KIND, HistoryImporter
from xcopilot.clock import Clock
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
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
