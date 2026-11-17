"""Задача очереди `quality.gate` (этап 11): провести черновики через ворота.

Обработчик зарегистрирован в `jobs.handlers.build_registry`; `complete`/`fail` делает воркер
(ADR 0022). Повтор задачи безопасен — ворота идемпотентны по ревизии. Недоступность моделей
ворота обрабатывают сами (режим «только линтер», needs_owner); неверный payload — постоянная
ошибка (задача сразу dead).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from xcopilot.jobs.queue import Job
from xcopilot.quality.loop import QualityGate
from xcopilot.quality.policy import Decision

QUALITY_GATE_KIND = "quality.gate"


def gate_payload(draft_ids: list[UUID], view: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"draft_ids": [str(d) for d in draft_ids]}


def gate_idempotency_key(draft_ids: list[UUID]) -> str:
    return "quality.gate:" + ",".join(sorted(str(d) for d in draft_ids))


async def handle_quality_gate(job: Job, *, gate: QualityGate) -> list[Decision]:
    """Ворота для каждого черновика задачи по порядку."""
    ids = [UUID(d) for d in job.payload["draft_ids"]]
    return [await gate.run_gate(job.account_id, d) for d in ids]
