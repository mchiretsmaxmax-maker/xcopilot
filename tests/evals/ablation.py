"""Абляция на эталоне (этап 15 v3.1, задача 12; ADR 0028): те же 30 тем с разделом знаний и без.

Правило (`eval_ablation_with_vs_without_knowledge`): с базой знаний оценка судьи по голосу не
ниже, по хуку — выше. Результат пишется в `baseline.json` → `ablation` (этап 15b).
Без знаний — тот же писатель с `NoKnowledge` (пакет знаний пустой, вариант 🧪 не делается).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from tests.evals.run_evals import (
    ABLATION_KEYS,
    EvalResult,
    Generate,
    GoldenTopic,
    evaluate,
    gate_generate,
)

VOICE, HOOK = ABLATION_KEYS


def ablation_summary(with_kb: EvalResult, without_kb: EvalResult) -> dict[str, Any]:
    """Блок `ablation` для baseline.json."""
    return {
        "with_knowledge": {k: with_kb.metrics.get(k) for k in ABLATION_KEYS},
        "without_knowledge": {k: without_kb.metrics.get(k) for k in ABLATION_KEYS},
        "n_topics": with_kb.n_topics,
    }


def ablation_verdict(summary: dict[str, Any]) -> list[str]:
    """Нарушения правила абляции; пустой список — база знаний помогает."""
    on, off = summary["with_knowledge"], summary["without_knowledge"]
    problems: list[str] = []
    if None in (on.get(VOICE), off.get(VOICE), on.get(HOOK), off.get(HOOK)):
        return ["нет оценок судьи: судья недоступен или темы не сгенерировались"]
    if on[VOICE] < off[VOICE]:
        problems.append(f"голос с базой хуже: {on[VOICE]:.3f} < {off[VOICE]:.3f}")
    if on[HOOK] <= off[HOOK]:
        problems.append(f"хук с базой не лучше: {on[HOOK]:.3f} ≤ {off[HOOK]:.3f}")
    return problems


async def ablate(
    topics: Sequence[GoldenTopic], with_kb: Generate, without_kb: Generate
) -> dict[str, Any]:
    return ablation_summary(await evaluate(topics, with_kb), await evaluate(topics, without_kb))


async def run_ablation(  # pragma: no cover - живой прогон (15b)
    app: Any, account_id: UUID, topics: Sequence[GoldenTopic]
) -> dict[str, Any]:
    """Абляция на собранном приложении: app.writer против того же писателя без знаний."""
    from xcopilot.retrieval.context_pack import NoKnowledge
    from xcopilot.writer.drafts import WriterService
    from xcopilot.writer.probation import NoProbation

    plain = WriterService(
        uow_factory=app.uow_factory,
        gateway=app.gateway,
        embedder=app.embedder,
        clock=app.clock,
        knowledge=NoKnowledge(),
        probation=NoProbation(),
    )
    return await ablate(
        topics, gate_generate(app, account_id), gate_generate(app, account_id, writer=plain)
    )
