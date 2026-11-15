"""Задача очереди `quality.gate` (этап 11): провести черновики запуска через ворота.

Воркера в проекте пока нет (как у писателя): обработчик вызывается напрямую. Повтор задачи
безопасен — ворота идемпотентны по ревизии. Ошибка БД/кода → `fail` (очередь повторит),
недоступность моделей ворота обрабатывают сами (режим «только линтер», needs_owner).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.quality.loop import QualityGate
from xcopilot.quality.policy import Decision

log = structlog.get_logger()

QUALITY_GATE_KIND = "quality.gate"


def gate_payload(draft_ids: list[UUID]) -> dict[str, Any]:
    return {"draft_ids": [str(d) for d in draft_ids]}


def gate_idempotency_key(draft_ids: list[UUID]) -> str:
    return "quality.gate:" + ",".join(sorted(str(d) for d in draft_ids))


async def handle_quality_gate(job: Job, *, gate: QualityGate) -> list[Decision]:
    """Ворота для каждого черновика задачи по порядку."""
    ids = [UUID(d) for d in job.payload.get("draft_ids", [])]
    return [await gate.run_gate(job.account_id, d) for d in ids]


async def process_gate_job(queue: JobQueue, job: Job, gate: QualityGate) -> list[Decision]:
    """Выполнить задачу и отметить её в очереди."""
    try:
        result = await handle_quality_gate(job, gate=gate)
    except Exception as exc:
        log.exception("quality_gate_job_failed", job_id=str(job.id))
        await queue.fail(job.id, type(exc).__name__)
        return []
    await queue.complete(job.id)
    return result
