"""Кандидаты знаний из правок и отказов (ТЗ 13, задачи 2–3, 6; ADR 0026).

Правки «до/после» и отказы «не мой голос» / «звучит как AI» → писатель (`teacher.from_reviews.v1`)
предлагает кандидатов только в слои `voice` (класс A) и `humanness` (класс C). Дальше код, а не
модель: проверяет метки, требует минимум два разных черновика и фразу из кавычек в самих правках,
отбрасывает похожее на удалённое владельцем за 30 дней, и передаёт в механизм 9A (эмбеддинг →
ближайшие → судья → слияние / связь / конфликт). Доказательства — `fit` с основанием `review`
по черновикам-правкам. Класс A ждёт «да» владельца, класс C сразу становится рабочим: линтер
предупреждает, а после подтверждения — ошибка.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from xcopilot.brain import lifecycle, merge, owner
from xcopilot.brain.compat import passport_text
from xcopilot.brain.context import BrainContext
from xcopilot.brain.detectors import RubricItem, eval_feature, eval_rubrics
from xcopilot.brain.evidence import VOICE_REJECT_CODES
from xcopilot.brain.extract import _embed, _passport
from xcopilot.brain.features import compute_features
from xcopilot.brain.schemas import DETECTOR, ExtractionResult, KnowledgeCandidate, RubricDetector
from xcopilot.brain.scoring import recompute
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo
from xcopilot.db.repos.learning import LearningRepo
from xcopilot.learning.diffing import EditSummary, classify_edit
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.metrics.events import EventType, emit
from xcopilot.quality.knowledge import phrases_of

PROMPT = "teacher.from_reviews.v1"
PURPOSE = "learning.propose"
LEARNING_PROPOSE_KIND = "learning.propose"
LAYERS = {"voice": "A", "humanness": "C"}
MIN_SIGNALS = 2  # меньше двух правок и отказов закономерности нет — модель не зовём
MIN_DRAFTS = 2  # доказательства — из разных черновиков
MAX_SIGNALS = 60
REMOVAL_LOOKBACK_DAYS = 30
REMOVAL_SCAN = 10  # правок на одно знание при поиске убранных приёмов
TEXT_CUT = 600


@dataclass(frozen=True)
class Signal:
    """Одна правка или один отказ с меткой M<n> для модели."""

    label: str
    kind: str  # edit | reject
    review_id: UUID
    draft_id: UUID
    before: str
    after: str | None
    created_at: Any
    summary: EditSummary | None = None
    reason: str | None = None
    comment: str | None = None


def _norm(text: str) -> str:
    return text.replace("’", "'").casefold()


def build_signals(rows: list[dict[str, Any]]) -> list[Signal]:
    """Правки с настоящей разницей, затем отказы по голосу; метки M1, M2… сквозные."""
    edits: list[Signal] = []
    rejects: list[Signal] = []
    for r in rows:
        if r["action"] == "edit" and r["text_after"]:
            summary = classify_edit(r["text_before"], r["text_after"])
            if summary.kinds:
                edits.append(_signal(r, "edit", summary))
        elif r["action"] == "reject" and r["reason_code"] in VOICE_REJECT_CODES:
            rejects.append(_signal(r, "reject", None))
    signals = (edits + rejects)[-MAX_SIGNALS:]
    return [Signal(**{**s.__dict__, "label": f"M{n}"}) for n, s in enumerate(signals, 1)]


def _signal(r: dict[str, Any], kind: str, summary: EditSummary | None) -> Signal:
    return Signal(
        label="",
        kind=kind,
        review_id=r["id"],
        draft_id=r["draft_id"],
        before=r["text_before"],
        after=r["text_after"],
        created_at=r["created_at"],
        summary=summary,
        reason=r["reason_code"],
        comment=r["comment"],
    )


def _cut(text: str) -> str:
    return text if len(text) <= TEXT_CUT else text[:TEXT_CUT] + "…"


def _pairs_text(signals: list[Signal]) -> str:
    lines: list[str] = []
    for s in signals:
        if s.kind != "edit" or s.summary is None:
            continue
        sm = s.summary
        lines.append(
            f"{s.label} edit kinds={','.join(sorted(sm.kinds))}"
            f" removed={list(sm.removed)} replaced={[f'{a}→{b}' for a, b in sm.replacements]}"
            f" at_start={'yes' if sm.removed_at_start else 'no'} casing={sm.casing or 'same'}\n"
            f"before: {_cut(s.before)}\nafter: {_cut(s.after or '')}"
        )
    return wrap_untrusted("\n\n".join(lines), "pairs") if lines else "нет"


def _rejections_text(signals: list[Signal]) -> str:
    lines = [
        f"{s.label} reject reason={s.reason} comment={s.comment or '-'}\ntext: {_cut(s.before)}"
        for s in signals
        if s.kind == "reject"
    ]
    return wrap_untrusted("\n\n".join(lines), "rejections") if lines else "нет"


def _supports(sig: Signal, phrase: str) -> bool:
    """Фраза есть в тексте до и (для правки) исчезла из текста после."""
    p = _norm(phrase)
    if p not in _norm(sig.before):
        return False
    return sig.kind == "reject" or p not in _norm(sig.after or "")


async def _verify(
    ctx: BrainContext, cand: KnowledgeCandidate, by_label: dict[str, Signal]
) -> list[Signal] | None:
    """Проверки кодом; None — кандидат отброшен."""
    if cand.layer not in LAYERS:
        return None
    supporting = [by_label[label] for label in dict.fromkeys(cand.supporting) if label in by_label]
    if len({s.draft_id for s in supporting}) < MIN_DRAFTS:
        return None
    phrases = phrases_of(cand.statement)
    if cand.layer == "humanness" and not phrases:
        return None  # мягкий запрет без фразы линтеру не за что зацепиться
    for phrase in phrases:
        if sum(_supports(s, phrase) for s in supporting) < MIN_DRAFTS:
            return None
    if await owner.is_suppressed(ctx, cand.statement):
        return None  # 9C: похожее удалено владельцем меньше 30 дней назад
    return supporting


async def _create(ctx: BrainContext, cand: KnowledgeCandidate, sources: list[Signal]) -> UUID:
    now = ctx.clock.now()
    scopes = {
        facet: list(getattr(cand.scope, attr))
        for attr, facet in (
            ("formats", "format"),
            ("goals", "goal"),
            ("niches", "niche"),
            ("langs", "lang"),
            ("archetypes", "archetype"),
        )
        if getattr(cand.scope, attr)
    }
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).create(
        kind=cand.kind,
        layer=cand.layer,
        element=cand.element,
        statement=cand.statement,
        guidance=cand.guidance,
        skeleton=cand.skeleton,
        target_metric="quality",
        detector=cand.detector.model_dump(),
        risk_class=LAYERS[cand.layer],
        origin="edits",
        created_at=now,
        updated_at=now,
        scopes=scopes,
        provenance={
            "prompt": PROMPT,
            "reviews": [str(s.review_id) for s in sources],
            "voice_compat": 1.0,
            "rationale": cand.rationale,
        },
    )
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.KNOWLEDGE_CANDIDATE_CREATED,
        entity=("knowledge_item", item.id),
        props={"item_id": str(item.id), "kind": cand.kind, "layer": cand.layer, "origin": "edits"},
    )
    return item.id


async def _evidence(ctx: BrainContext, item_id: UUID, sources: list[Signal]) -> int:
    repo = BrainRepo(ctx.session, ctx.account_id)
    written = 0
    for s in sources:
        evidence_id = await repo.insert_evidence(
            item_id=item_id,
            draft_id=s.draft_id,
            dimension="fit",
            basis="review",
            polarity=1,
            w_source=1.0,
            w_scope=1.0,
            w_magnitude=1.0,
            observed_at=s.created_at,
            created_at=ctx.clock.now(),
        )
        if evidence_id is not None:
            written += 1
            await emit(
                ctx.session,
                account_id=ctx.account_id,
                type=EventType.KNOWLEDGE_EVIDENCE_ADDED,
                entity=("knowledge_item", item_id),
                props={
                    "item_id": str(item_id),
                    "evidence_id": str(evidence_id),
                    "dimension": "fit",
                    "basis": "review",
                    "polarity": 1,
                },
            )
    return written


async def _ingest(
    ctx: BrainContext, cand: KnowledgeCandidate, sources: list[Signal], existing: list[UUID]
) -> UUID:
    """Новое знание → слияние (9A) → доказательства → класс C сразу в работу."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    if cand.same_as_existing is not None:
        n = int(cand.same_as_existing[1:])
        if 1 <= n <= len(existing):
            await _evidence(ctx, existing[n - 1], sources)
            await recompute(ctx, existing[n - 1])
            return existing[n - 1]
    new_id = await _create(ctx, cand, sources)
    vec = await _embed(ctx, new_id, f"{cand.statement} {cand.guidance}")
    target = new_id
    for near in await merge.nearest(ctx, vec, ctx.config.judge.nearest_k, exclude=[new_id]):
        relation = await merge.relate(ctx, cand.statement, near)
        if relation.kind == "different":
            continue
        if relation.kind == "same":
            target = near.item_id
        await merge.apply_relation(ctx, new_id, near, relation)
        break
    await _evidence(ctx, target, sources)
    row = await repo.item(target)
    if row is not None and row["status"] == "candidate" and row["layer"] == "humanness":
        await lifecycle.apply(
            ctx,
            lifecycle.Transition(
                target,
                "candidate",
                "active",
                "edits_soft_warn",
                "мягкий запрет из правок: линтер предупреждает сразу",
            ),
        )
    await recompute(ctx, target)
    return target


# ---------------------------------------------------------------- убранные владельцем приёмы


def _removed_by_feature(detector: dict[str, Any], before: str, after: str) -> bool:
    return (
        eval_feature(detector, {"features": compute_features(before, "post")}) == 1.0
        and eval_feature(detector, {"features": compute_features(after, "post")}) == 0.0
    )


async def _device_removed(
    ctx: BrainContext, item_id: UUID, detector: dict[str, Any], before: str, after: str
) -> bool:
    """Приём был в тексте до правки и пропал после (feature — кодом, rubric — судьёй)."""
    det = DETECTOR.validate_python(detector)
    if isinstance(det, RubricDetector):
        item = RubricItem(item_id, det.question)
        cfg = ctx.config.detectors
        was = (await eval_rubrics(ctx, [item], before, det.on)).get(item_id, 0.5)
        now = (await eval_rubrics(ctx, [item], after, det.on)).get(item_id, 0.5)
        return was >= cfg.rubric_present and now <= cfg.rubric_absent
    return _removed_by_feature(detector, before, after)


async def detect_removed_devices(ctx: BrainContext, review_id: UUID) -> list[UUID]:
    """Знания, применённые в черновике, приём которых владелец убрал этой правкой."""
    repo = LearningRepo(ctx.session, ctx.account_id)
    review = await repo.review(review_id)
    if review is None or review["action"] != "edit" or not review["text_after"]:
        return []
    out: list[UUID] = []
    for row in await repo.removal_candidates(review["draft_id"]):
        if await _device_removed(
            ctx, row["id"], row["detector"], review["text_before"], review["text_after"]
        ):
            out.append(row["id"])
    return out


async def _record_removals(ctx: BrainContext, rows: list[dict[str, Any]]) -> int:
    """Второй и каждый следующий раз, когда убран применённый приём, — минус к совместимости."""
    repo = LearningRepo(ctx.session, ctx.account_id)
    brain = BrainRepo(ctx.session, ctx.account_id)
    since = ctx.clock.now() - timedelta(days=REMOVAL_LOOKBACK_DAYS)
    items: set[UUID] = set()
    for r in rows:
        if r["action"] == "edit":
            items.update(await detect_removed_devices(ctx, r["id"]))
    weight = abs(ctx.config.weights.review["heavy_rewrite"])
    written = 0
    for item_id in items:
        row = await brain.item(item_id)
        if row is None:
            continue
        removals = [
            r
            for r in await repo.edit_reviews_applying(item_id, since=since, limit=REMOVAL_SCAN)
            if await _device_removed(
                ctx, item_id, row["detector"], r["text_before"], r["text_after"]
            )
        ]
        for r in removals[1:]:
            evidence_id = await brain.insert_evidence(
                item_id=item_id,
                draft_id=r["draft_id"],
                dimension="fit",
                basis="review",
                polarity=-1,
                w_source=weight,
                w_scope=1.0,
                w_magnitude=1.0,
                observed_at=r["created_at"],
                created_at=ctx.clock.now(),
            )
            written += evidence_id is not None
        await recompute(ctx, item_id)
    return written


# ---------------------------------------------------------------- проход


@dataclass(frozen=True)
class RunResult:
    candidates: list[KnowledgeCandidate]
    item_ids: list[UUID]
    reviews: int


async def run(ctx: BrainContext) -> RunResult:
    """Проход обучения: убранные приёмы → кандидаты из правок и отказов → механизм 9A."""
    learning = LearningRepo(ctx.session, ctx.account_id)
    rows = await learning.reviews_after(await learning.last_run_at())
    await _record_removals(ctx, rows)
    signals = build_signals(rows)
    if len(signals) < MIN_SIGNALS:
        return RunResult([], [], len(rows))
    repo = BrainRepo(ctx.session, ctx.account_id)
    existing = await repo.existing_for_extract(ctx.config.extract.existing_max)
    niche = await repo.account_niche()
    passport = await _passport(ctx)
    variables = {
        "account": f"niche: {niche['niche'] or 'unknown'} · lang: {niche['lang']}\n"
        + (passport_text(passport) if passport else "voice: паспорта ещё нет\nhard_rules: []"),
        "existing": "\n".join(
            f"K{n} [{e['kind']} / {e['layer']} / {e['element']}] {e['statement']}"
            for n, e in enumerate(existing, 1)
        )
        or "нет",
        "pairs": _pairs_text(signals),
        "rejections": _rejections_text(signals),
    }
    result = await ctx.llm.generate(
        role=Role.WRITER,
        prompt_key=PROMPT,
        variables=variables,
        schema=ExtractionResult,
        account_id=ctx.account_id,
        purpose=PURPOSE,
    )
    by_label = {s.label: s for s in signals}
    existing_ids = [e["id"] for e in existing]
    accepted: list[KnowledgeCandidate] = []
    item_ids: list[UUID] = []
    for cand in result.candidates:
        sources = await _verify(ctx, cand, by_label)
        if sources is None:
            continue
        item_ids.append(await _ingest(ctx, cand, sources, existing_ids))
        accepted.append(cand)
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.LEARNING_PROPOSED,
        props={"reviews": len(rows), "candidates": len(accepted)},
        ts=ctx.clock.now(),
    )
    return RunResult(accepted, item_ids, len(rows))


async def propose(ctx: BrainContext) -> list[KnowledgeCandidate]:
    """Принятые кандидаты прохода (знания уже записаны в базу)."""
    return (await run(ctx)).candidates
