"""Задача очереди `quality.gate` (этап 11): провести черновики через ворота.

Обработчик зарегистрирован в `jobs.handlers.build_registry`; `complete`/`fail` делает воркер
(ADR 0022). Повтор задачи безопасен — ворота идемпотентны по ревизии. Недоступность моделей
ворота обрабатывают сами (режим «только линтер», needs_owner); неверный payload — постоянная
ошибка (задача сразу dead).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from xcopilot.jobs.queue import Job
from xcopilot.quality.loop import QualityGate
from xcopilot.quality.policy import Decision

QUALITY_GATE_KIND = "quality.gate"


def gate_payload(draft_ids: list[UUID], view: dict[str, Any] | None = None) -> dict[str, Any]:
    """Payload `quality.gate`; `view` из `writer.generate` уходит дальше к показу (ADR 0032)."""
    payload: dict[str, Any] = {"draft_ids": [str(d) for d in draft_ids]}
    if view is not None:
        payload["view"] = view
    return payload


def gate_idempotency_key(draft_ids: list[UUID]) -> str:
    return "quality.gate:" + ",".join(sorted(str(d) for d in draft_ids))


async def handle_quality_gate(job: Job, *, gate: QualityGate) -> list[Decision]:
    """До трёх проверок одновременно, результаты — в порядке payload."""
    ids = [UUID(d) for d in job.payload["draft_ids"]]
    slots = asyncio.Semaphore(3)

    async def check(draft_id: UUID) -> Decision:
        async with slots:
            return await gate.run_gate(job.account_id, draft_id)

    # Повтор ID в payload использует одну проверку.
    checks = {draft_id: asyncio.create_task(check(draft_id)) for draft_id in dict.fromkeys(ids)}
    try:
        await asyncio.gather(*checks.values())
    except BaseException:
        for task in checks.values():
            task.cancel()
        await asyncio.gather(*checks.values(), return_exceptions=True)
        raise
    return [checks[draft_id].result() for draft_id in ids]
