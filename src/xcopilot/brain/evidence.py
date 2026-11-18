"""Доказательства знаний (ТЗ v3.1, 9A, задачи 3 и 11): наличие × исход → строка с весами.

Множители (раздел 2.4): `w_source` — надёжность источника исхода, `w_scope` — близость (свой
пост 2.0, своя ниша 1.0, ниша неизвестна 0.8, чужая 0.5), `w_magnitude` — сила исхода,
`w_author` — 1/k за k-е доказательство того же автора (свои посты не дисконтируются).
Строки не меняются: ошибочные отзываются, веса пересчитываются функцией БД.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.brain.detectors import detect
from xcopilot.brain.scoring import recompute
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.sources import SourcesRepo
from xcopilot.metrics.events import EventType, emit

SUPPORT = frozenset({"hit", "above"})
CONTRA = frozenset({"below", "flop"})
KNOWN_TIERS = SUPPORT | CONTRA | {"normal"}
# Коды причин отказа «не мой голос» и «звучит как AI» (кнопки этапа 12).
VOICE_REJECT_CODES = frozenset({"not_my_voice", "sounds_ai"})


def _polarity(tier: str) -> int:
    return 1 if tier in SUPPORT else -1 if tier in CONTRA else 0


def _scope(ctx: BrainContext, material: Mapping[str, Any]) -> float:
    w = ctx.config.weights.scope
    if material["stance"] == "own":
        return w["own"]
    author, account = material.get("author_niche"), material.get("account_niche")
    if not author or not account:
        return w["unknown_niche"]
    return w["same_niche"] if author == account else w["other_niche"]


def _declares(material: Mapping[str, Any]) -> bool:
    is_article = material.get("kind") == "article" or material.get("format") == "article"
    return is_article and bool(material.get("declared_claims"))


async def material_evidence(
    ctx: BrainContext,
    material: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    detector_score: float | None = None,
) -> UUID | None:
    """Одна строка eff по материалу и знанию; нечего записывать или повтор — None."""
    if material.get("author_trust") == -1 or material.get("removed_at") is not None:
        return None
    own = material["stance"] == "own"
    if item["layer"] == "voice" and not own:
        return None  # голос учится только на своих постах (и триггер БД это держит)
    cfg = ctx.config.weights
    target = item["target_metric"]
    tier = material.get(f"tier_{target}") if target != "quality" else None
    if not own and _declares(material):
        basis, polarity = "declared", 1
        w_source, w_magnitude = cfg.source["declared"], 1.0
    else:
        if tier not in KNOWN_TIERS:
            return None
        polarity = _polarity(tier)
        w_magnitude = cfg.magnitude[tier]
        if own:
            basis, w_source = "own_outcome", cfg.source["own_sync"]
        elif material.get("outcome_basis") == "owner_pick":
            basis, w_source = "owner_pick", cfg.source["owner_pick"]
        else:
            basis = "observed_outcome"
            w_source = cfg.source.get(str(material.get("signal_origin")), cfg.source["owner_label"])
    repo = BrainRepo(ctx.session, ctx.account_id)
    author_id = material.get("author_id")
    w_author = 1.0
    if author_id is not None and not own:
        w_author = 1.0 / (await repo.author_evidence_count(item["id"], author_id) + 1)
    observed: datetime = material.get("posted_at") or material["created_at"]
    evidence_id = await repo.insert_evidence(
        item_id=item["id"],
        source_id=material["id"],
        dimension="eff",
        basis=basis,
        polarity=polarity,
        detector_score=detector_score,
        outcome_tier=tier,
        target_metric=None if target == "quality" else target,
        format=material.get("format"),
        author_id=author_id,
        w_source=w_source,
        w_scope=_scope(ctx, material),
        w_magnitude=w_magnitude,
        w_author=round(w_author, 4),
        observed_at=observed,
        created_at=ctx.clock.now(),
    )
    if evidence_id is not None:
        await emit(
            ctx.session,
            account_id=ctx.account_id,
            type=EventType.KNOWLEDGE_EVIDENCE_ADDED,
            entity=("knowledge_item", item["id"]),
            props={
                "item_id": str(item["id"]),
                "evidence_id": str(evidence_id),
                "dimension": "eff",
                "basis": basis,
                "polarity": polarity,
            },
        )
    return evidence_id


async def record_from_material(ctx: BrainContext, source_id: UUID) -> int:
    """Детекторы на материале → доказательства eff; вернуть число новых строк."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    material = await repo.material(source_id)
    if material is None or material.get("analysis_id") is None:
        return 0
    if material.get("author_trust") == -1 or material.get("removed_at") is not None:
        return 0
    items = {r["id"]: r for r in await repo.detector_items()}
    written = 0
    for hit in await detect(ctx, source_id):
        if not (hit.present and hit.item_id in items):
            continue
        if await material_evidence(ctx, material, items[hit.item_id], detector_score=hit.score):
            written += 1
    return written


async def revise_material(ctx: BrainContext, source_id: UUID) -> int:
    """Исход или отношение материала изменились: отозвать его доказательства и записать заново."""
    raise NotImplementedError


async def record_own_outcome(ctx: BrainContext, source_id: UUID) -> int:
    """Исход своего поста → доказательства own_outcome (вес 2.0, без скидки за автора)."""
    material = await BrainRepo(ctx.session, ctx.account_id).material(source_id)
    if material is None or material["stance"] != "own":
        return 0
    return await record_from_material(ctx, source_id)


async def record_from_review(ctx: BrainContext, review_id: UUID) -> int:
    """Решение владельца по черновику → доказательства fit применённых знаний."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    review = await repo.review(review_id)
    if review is None:
        return 0
    w = ctx.config.weights.review
    action, distance = review["action"], review.get("edit_distance")
    if action == "approve" and not distance:
        weight = w["approved_clean"]
    elif (
        action in ("approve", "edit")
        and (distance or 0) >= ctx.config.thresholds.heavy_rewrite_distance
    ):
        weight = w["heavy_rewrite"]
    elif action == "reject" and review.get("reason_code") in VOICE_REJECT_CODES:
        weight = w["voice_reject"]
    else:
        return 0  # другие причины отказа не учитываются
    written = 0
    for row in await repo.applied_items(review["draft_id"]):
        evidence_id = await repo.insert_evidence(
            item_id=row["item_id"],
            draft_id=review["draft_id"],
            dimension="fit",
            basis="review",
            polarity=1 if weight > 0 else -1,
            w_source=abs(weight),
            w_scope=1.0,
            w_magnitude=1.0,
            observed_at=review["created_at"],
            created_at=ctx.clock.now(),
        )
        if evidence_id is not None:
            written += 1
            await emit(
                ctx.session,
                account_id=ctx.account_id,
                type=EventType.KNOWLEDGE_EVIDENCE_ADDED,
                entity=("knowledge_item", row["item_id"]),
                props={
                    "item_id": str(row["item_id"]),
                    "evidence_id": str(evidence_id),
                    "dimension": "fit",
                    "basis": "review",
                    "polarity": 1 if weight > 0 else -1,
                },
            )
    return written


async def revoke(
    ctx: BrainContext,
    *,
    source_id: UUID | None = None,
    author_id: UUID | None = None,
    reason: str,
) -> int:
    """Отозвать доказательства материала или автора и пересчитать веса; вернуть число.

    `reason="source_removed"` ещё и убирает материал, `"author_blocked"` — блокирует автора
    (новые материалы автора больше не учатся).
    """
    if source_id is None and author_id is None:
        raise ValueError("revoke: нужен source_id или author_id")
    repo = BrainRepo(ctx.session, ctx.account_id)
    now = ctx.clock.now()
    items = await repo.revoke_evidence(
        at=now, reason=reason, source_id=source_id, author_id=author_id
    )
    if source_id is not None and reason == "source_removed":
        sources = SourcesRepo(ctx.session, ctx.account_id)
        if (await sources.require(source_id)).removed_at is None:
            await sources.update(source_id, removed_at=now)
    if author_id is not None and reason == "author_blocked":
        await repo.block_author(author_id, reason)
    for item_id in sorted(set(items)):
        await recompute(ctx, item_id, now)
    return len(items)
