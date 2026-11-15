"""Извлечение знаний ролью writer (ТЗ v3.1, 9A, задача 4) и приём кандидатов (задачи 5–7).

Пачка — до 25 разборов одного формата; запускается, когда накопилось 10 новых разборов формата
или самый старый ждёт больше суток. Своя импортированная история идёт контрастной выборкой:
верхние и нижние 20% по исходу. Модель видит разборы, а не сырые тексты. Обработанная пачка
отмечается событием `brain.extraction_done` — повторный прогон её не берёт.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from xcopilot.brain import merge, owner, sanitize
from xcopilot.brain.compat import passport_text, voice_compat
from xcopilot.brain.context import BrainContext
from xcopilot.brain.evidence import material_evidence
from xcopilot.brain.schemas import ALLOWED_PATHS, ExtractionResult, KnowledgeCandidate
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.knowledge import KnowledgeEmbeddingsRepo, KnowledgeItemsRepo
from xcopilot.db.repos.passports import PassportsRepo
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.metrics.events import EventType, emit

EXTRACT_PROMPT = "brain.extract_knowledge.v1"
PURPOSE = "brain.extract"
HISTORY_ORIGIN = "history_backfill"
_TIER_RANK = {"flop": 0, "below": 1, "normal": 2, "above": 3, "hit": 4}
_RISK = {"voice": "A", "humanness": "C"}
_SCOPE_FACETS = {
    "formats": "format",
    "goals": "goal",
    "niches": "niche",
    "langs": "lang",
    "archetypes": "archetype",
}


@dataclass
class Batch:
    """Пачка разборов одного формата: M1..Mn → source_id, K1..Kn → знания."""

    account_id: UUID
    format: str
    materials: list[UUID]
    own_history: bool = False
    existing: list[UUID] = field(default_factory=list)
    covers: list[UUID] = field(default_factory=list)  # что отметить обработанным


def _chunks(ids: Sequence[UUID], size: int) -> list[list[UUID]]:
    return [list(ids[i : i + size]) for i in range(0, len(ids), size)]


def _rank(row: Mapping[str, Any]) -> float:
    pct = row.get("pct_engagement")
    if pct is not None:
        return float(pct)
    return _TIER_RANK.get(str(row.get("tier_engagement")), 2) / 4


def _contrast(rows: Sequence[Mapping[str, Any]], share: float) -> list[UUID]:
    rated = sorted((r for r in rows if r.get("tier_engagement") is not None), key=_rank)
    if not rated:
        return []
    k = max(1, round(len(rated) * share))
    picked = rated[:k] + rated[-k:] if len(rated) > 2 * k else rated
    return [r["id"] for r in picked]


async def contrast_sample(ctx: BrainContext, share: float = 0.2) -> list[UUID]:
    """Свои посты истории: верхние и нижние доли по исходу."""
    rows = await BrainRepo(ctx.session, ctx.account_id).pending_materials()
    history = [r for r in rows if r["stance"] == "own" and r["origin"] == HISTORY_ORIGIN]
    return _contrast(history, share)


async def build_batches(ctx: BrainContext) -> list[Batch]:
    """Новые разборы по форматам, пачки до 25; своя история — только контрастная выборка."""
    cfg = ctx.config.extract
    rows = await BrainRepo(ctx.session, ctx.account_id).pending_materials()
    history = [r for r in rows if r["stance"] == "own" and r["origin"] == HISTORY_ORIGIN]
    batches: list[Batch] = []
    if history:
        sample = _contrast(history, cfg.contrast_share)
        covers = [r["id"] for r in history]
        for part in _chunks(sample, cfg.batch_max):
            batches.append(Batch(ctx.account_id, "post", part, own_history=True, covers=covers))
    by_format: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if not (r["stance"] == "own" and r["origin"] == HISTORY_ORIGIN):
            by_format[r["format"]].append(r)
    day_ago = ctx.clock.now() - timedelta(days=1)
    for fmt, group in sorted(by_format.items()):
        if len(group) < cfg.min_new_per_format and min(r["created_at"] for r in group) > day_ago:
            continue
        for part in _chunks([r["id"] for r in group], cfg.batch_max):
            batches.append(Batch(ctx.account_id, fmt, part, covers=part))
    return batches


def _compact(row: Mapping[str, Any]) -> str:
    """Разбор без дословных цитат: ключи словарей, признаки, гипотезы без цитат."""
    data = {
        "archetype": row.get("archetype"),
        "hook_type": row.get("hook_type"),
        "structure": row.get("structure"),
        "closer": row.get("closer"),
        "tone": list(row.get("tone") or []),
        "devices": list(row.get("devices") or []),
        "outline": [s.get("role") for s in (row.get("structure_outline") or [])],
        "features": dict(row.get("features") or {}),
        "why": [
            {"element": w.get("element"), "claim": w.get("claim")}
            for w in (row.get("why_hypotheses") or [])
        ],
        "declared": [c.get("statement") for c in (row.get("declared_claims") or [])],
        "human_markers": len(row.get("human_markers") or []),
        "ai_markers": len(row.get("ai_markers") or []),
        "quality_flags": list(row.get("quality_flags") or []),
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


async def _passport(ctx: BrainContext) -> dict[str, Any] | None:
    active = await PassportsRepo(ctx.session, ctx.account_id).get_active()
    if active is None:
        return None
    body = dict(active.body or {})
    return {"summary": body.get("summary") or body, "hard_rules": active.hard_rules or []}


async def extract(ctx: BrainContext, batch: Batch) -> ExtractionResult:
    """Вызов writer с промптом brain.extract_knowledge.v1: разборы, а не сырые тексты."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    materials = await repo.batch_materials(batch.materials)
    batch.materials = [m["id"] for m in materials]
    existing = await repo.existing_for_extract(ctx.config.extract.existing_max)
    batch.existing = [e["id"] for e in existing]
    passport = await _passport(ctx)
    niche = await repo.account_niche()
    lines = []
    for n, m in enumerate(materials, 1):
        outcome = " ".join(
            f"{g}={m.get(f'tier_{g}') or 'unknown'}"
            for g in ("reach", "engagement", "replies", "saves")
        )
        reliability = m.get("reliability")
        lines.append(
            f"M{n} stance={m['stance']} format={m['format']} outcome={outcome}"
            f" reliability={'unknown' if reliability is None else float(reliability)}\n"
            f"analysis: {_compact(m)}"
        )
    variables = {
        "account": f"niche: {niche['niche'] or 'unknown'} · lang: {niche['lang']}\n"
        + (passport_text(passport) if passport else "voice: паспорта ещё нет\nhard_rules: []"),
        "paths": ", ".join(sorted(ALLOWED_PATHS)),
        "existing": "\n".join(
            f"K{n} [{e['kind']} / {e['layer']} / {e['element']}] {e['statement']}"
            for n, e in enumerate(existing, 1)
        )
        or "нет",
        "materials": wrap_untrusted("\n".join(lines), "materials"),
    }
    return await ctx.llm.generate(
        role=Role.WRITER,
        prompt_key=EXTRACT_PROMPT,
        variables=variables,
        schema=ExtractionResult,
        account_id=ctx.account_id,
        purpose=PURPOSE,
    )


def _labels(labels: Sequence[str], ids: Sequence[UUID]) -> list[UUID]:
    out: list[UUID] = []
    for label in labels:
        n = int(label[1:])
        if 1 <= n <= len(ids) and ids[n - 1] not in out:
            out.append(ids[n - 1])
    return out


async def _evidence(ctx: BrainContext, item: Mapping[str, Any], sources: Sequence[UUID]) -> int:
    repo = BrainRepo(ctx.session, ctx.account_id)
    written = 0
    for sid in sources:
        material = await repo.material(sid)
        if material is not None and await material_evidence(ctx, material, item):
            written += 1
    return written


async def _create(
    ctx: BrainContext,
    cand: KnowledgeCandidate,
    layer: str,
    compat: float,
    sources: Sequence[UUID],
) -> UUID:
    now = ctx.clock.now()
    target = "quality" if layer in ("voice", "humanness") else cand.target_metric
    scopes = {
        facet: list(getattr(cand.scope, attr))
        for attr, facet in _SCOPE_FACETS.items()
        if getattr(cand.scope, attr)
    }
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).create(
        kind=cand.kind,
        layer=layer,
        element=cand.element,
        statement=cand.statement,
        guidance=cand.guidance,
        skeleton=cand.skeleton,
        target_metric=target,
        detector=cand.detector.model_dump(),
        risk_class=_RISK.get(layer, "B"),
        origin="materials",
        created_at=now,
        updated_at=now,
        scopes=scopes,
        provenance={
            "prompt": EXTRACT_PROMPT,
            "materials": [str(s) for s in sources],
            "voice_compat": compat,
            "rationale": cand.rationale,
        },
    )
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.KNOWLEDGE_CANDIDATE_CREATED,
        entity=("knowledge_item", item.id),
        props={"item_id": str(item.id), "kind": cand.kind, "layer": layer, "origin": "materials"},
    )
    return item.id


async def _embed(ctx: BrainContext, item_id: UUID, text: str) -> list[float]:
    vec = ctx.embedder.embed([text])[0]
    model = await BrainRepo(ctx.session, ctx.account_id).active_embedding_model()
    if model is not None:
        await KnowledgeEmbeddingsRepo(ctx.session, ctx.account_id).upsert(
            item_id, model, version=1, vec=vec
        )
    return vec


async def ingest_candidates(
    ctx: BrainContext, result: ExtractionResult, batch: Batch
) -> list[UUID]:
    """Проверка, слияние и запись кандидатов с доказательствами; вернуть затронутые знания."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    stances = {m["id"]: m["stance"] for m in await repo.batch_materials(batch.materials)}
    passport = await _passport(ctx)
    touched: list[UUID] = []
    for cand in result.candidates:
        supporting = _labels(cand.supporting, batch.materials)
        contradicting = _labels(cand.contradicting, batch.materials)
        if len(supporting) < ctx.config.extract.min_supporting:
            continue
        if await owner.is_suppressed(ctx, cand.statement):
            continue  # 9C: владелец удалил похожее знание меньше 30 дней назад
        own_support = [s for s in supporting if stances.get(s) == "own"]
        if cand.same_as_existing is not None:
            n = int(cand.same_as_existing[1:])
            existing = batch.existing[n - 1] if 1 <= n <= len(batch.existing) else None
            row = await repo.item(existing) if existing is not None else None
            if row is not None:
                await _evidence(ctx, row, supporting + contradicting)
                if row["id"] not in touched:
                    touched.append(row["id"])
                continue
        verdict = await sanitize.check(ctx, cand, [stances.get(s, "?") for s in supporting])
        if not verdict.ok:
            continue
        layer = verdict.layer
        if layer == "voice" and len(own_support) < len(supporting):
            continue  # голос — только из своих материалов
        if layer == "voice" and len(own_support) < ctx.config.extract.min_supporting:
            continue
        compat = 1.0 if layer == "voice" else await voice_compat(ctx, cand.guidance, passport)
        new_id = await _create(ctx, cand, layer, compat, supporting)
        vec = await _embed(ctx, new_id, f"{cand.statement} {cand.guidance}")
        target_id = new_id
        for near in await merge.nearest(ctx, vec, ctx.config.judge.nearest_k, exclude=[new_id]):
            relation = await merge.relate(ctx, cand.statement, near)
            if relation.kind == "different":
                continue
            if relation.kind == "same":
                target_id = near.item_id
            await merge.apply_relation(ctx, new_id, near, relation)
            break
        row = await repo.item(target_id)
        assert row is not None
        await _evidence(ctx, row, supporting + contradicting)
        if target_id not in touched:
            touched.append(target_id)
    return touched


async def run(ctx: BrainContext) -> list[UUID]:
    """Все пачки аккаунта: извлечь, принять, отметить пачку обработанной."""
    touched: list[UUID] = []
    for batch in await build_batches(ctx):
        covers = list(batch.covers or batch.materials)
        result = await extract(ctx, batch)
        items = await ingest_candidates(ctx, result, batch)
        touched += [i for i in items if i not in touched]
        await emit(
            ctx.session,
            account_id=ctx.account_id,
            type=EventType.BRAIN_EXTRACTION_DONE,
            props={
                "format": batch.format,
                "materials": [str(m) for m in covers],
                "candidates": len(result.candidates),
            },
        )
    return touched
