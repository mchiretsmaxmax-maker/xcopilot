"""Детекторы знаний (ТЗ v3.1, 9A, задача 2): feature — кодом по полям разбора, rubric — судьёй.

Рубрики: не больше 30 знаний на материал (ближайших по вектору к тексту), вопросы — пачками
по 10, текст — в обёртке недоверенных данных. Ответ ≥ 0.7 — знание есть, ≤ 0.3 — нет,
между — не учитывается.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from xcopilot.brain import judge_questions
from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import DETECTOR, FeatureCondition, FeatureDetector, RubricDetector
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.llm.schemas import NoulA, Question
from xcopilot.llm.untrusted import wrap_untrusted

Part = Literal["first_line", "full_text"]
PURPOSE = "brain.detect"
PARTS: tuple[Part, ...] = ("first_line", "full_text")


@dataclass(frozen=True)
class RubricItem:
    """Знание с детектором-рубрикой: id и утверждение для судьи."""

    item_id: UUID
    question: str


@dataclass(frozen=True)
class Hit:
    """Результат детектора: знание есть в тексте (present) или точно нет."""

    item_id: UUID
    present: bool
    score: float
    by: Literal["feature", "rubric"]


_MISSING = object()


def _value(analysis: Mapping[str, Any], path: str) -> Any:
    head, _, tail = path.partition(".")
    value = analysis.get(head, _MISSING)
    if tail:
        value = value.get(tail, _MISSING) if isinstance(value, Mapping) else _MISSING
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _holds(cond: FeatureCondition, analysis: Mapping[str, Any]) -> bool:
    value = _value(analysis, cond.path)
    if value is _MISSING or value is None:
        return False
    match cond.op:
        case "eq":
            return bool(value == cond.value)
        case "in":
            return isinstance(cond.value, list) and value in cond.value
        case "contains":
            if isinstance(value, list | tuple):
                return cond.value in value
            return isinstance(value, str) and isinstance(cond.value, str) and cond.value in value
        case "gte" | "lte":
            left, right = _number(value), _number(cond.value)
            if left is None or right is None:
                return False
            return left >= right if cond.op == "gte" else left <= right
        case "is_true":
            return value is True
        case "is_false":
            return value is False
    return False  # pragma: no cover - Literal исчерпан


def eval_feature(
    detector: FeatureDetector | Mapping[str, Any], analysis: Mapping[str, Any]
) -> float:
    """1.0 — все условия выполнены, 0.0 — нет. Неизвестное поле — ValueError."""
    if not isinstance(detector, FeatureDetector):
        detector = FeatureDetector.model_validate(detector)
    return 1.0 if all(_holds(c, analysis) for c in detector.all) else 0.0


def _part(text: str, part: Part) -> str:
    if part == "first_line":
        return next((line for line in text.splitlines() if line.strip()), "")
    return text


async def eval_rubrics(
    ctx: BrainContext, items: Sequence[RubricItem], text: str, part: Part
) -> dict[UUID, float]:
    """Вероятность «да» судьи по каждому знанию; вопросы — пачками по 10."""
    size = ctx.config.detectors.rubric_batch
    state = wrap_untrusted(_part(text, part), "material")
    out: dict[UUID, float] = {}
    for start in range(0, len(items), size):
        chunk = items[start : start + size]
        questions: dict[str, Question] = {
            f"k{i}": judge_questions.rubric(it.question) for i, it in enumerate(chunk)
        }
        verdict = await ctx.llm.decide(
            state=state, questions=questions, account_id=ctx.account_id, purpose=PURPOSE
        )
        for i, it in enumerate(chunk):
            answer = verdict.answers.get(f"k{i}")
            if isinstance(answer, NoulA):
                out[it.item_id] = answer.p
    return out


def analysis_view(material: Mapping[str, Any]) -> dict[str, Any]:
    """Поля разбора, доступные детекторам feature (ALLOWED_PATHS)."""
    return {
        "format": material.get("format"),
        "archetype": material.get("archetype"),
        "hook_type": material.get("hook_type"),
        "structure": material.get("structure"),
        "closer": material.get("closer"),
        "devices": list(material.get("devices") or []),
        "tone": list(material.get("tone") or []),
        "features": dict(material.get("features") or {}),
    }


async def detect(ctx: BrainContext, source_id: UUID) -> list[Hit]:
    """Детекторы всех рабочих знаний на текущем разборе материала; неуверенные — отброшены."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    material = await repo.material(source_id)
    if material is None or material.get("analysis_id") is None:
        return []
    view = analysis_view(material)
    hits: list[Hit] = []
    rubrics: dict[Part, list[RubricItem]] = {"first_line": [], "full_text": []}
    for row in await repo.detector_items():
        det = DETECTOR.validate_python(row["detector"])
        if isinstance(det, FeatureDetector):
            score = eval_feature(det, view)
            hits.append(Hit(row["id"], score == 1.0, score, "feature"))
        elif isinstance(det, RubricDetector):
            rubrics[det.on].append(RubricItem(row["id"], det.question))
    all_rubrics = rubrics["first_line"] + rubrics["full_text"]
    if not all_rubrics:
        return hits
    text = material.get("text") or ""
    chosen = await _nearest_rubrics(ctx, repo, all_rubrics, text)
    cfg = ctx.config.detectors
    for part in PARTS:
        items = [it for it in rubrics[part] if it.item_id in chosen]
        if not items:
            continue
        scores = await eval_rubrics(ctx, items, text, part)
        for item_id, p in scores.items():
            if p >= cfg.rubric_present:
                hits.append(Hit(item_id, True, p, "rubric"))
            elif p <= cfg.rubric_absent:
                hits.append(Hit(item_id, False, p, "rubric"))
    return hits


async def _nearest_rubrics(
    ctx: BrainContext, repo: BrainRepo, items: Sequence[RubricItem], text: str
) -> set[UUID]:
    """Не больше rubric_max_items знаний: ближайшие по вектору к тексту материала."""
    limit = ctx.config.detectors.rubric_max_items
    ids = [it.item_id for it in items]
    if len(ids) <= limit:
        return set(ids)
    vec = (await asyncio.to_thread(ctx.embedder.embed, [text]))[0]
    near = await repo.nearest(vec, limit=limit, ids=ids)
    chosen = {r["item_id"] for r in near}
    for item_id in ids:  # знания без вектора — после ближайших, по качеству
        if len(chosen) >= limit:
            break
        chosen.add(item_id)
    return chosen
