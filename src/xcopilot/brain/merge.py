"""Слияние кандидата с базой (ТЗ v3.1, 9A, задача 6): ближайшие → отношение → действие.

До 5 рабочих знаний ближе `related_threshold` (порог модели эмбеддингов) → судья: то же /
уже / шире / противоречит / другое. «То же» (≥ 0.7) — слияние в старшее знание, доказательства
переходят; «противоречит» (≥ 0.6) — открытое противоречие, обе стороны уходят из пакета;
«уже»/«шире» — связь. Противоречие решает владелец или разрыв в 3 своих доказательства.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from xcopilot.brain import judge_questions, lifecycle
from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo, KnowledgeLinksRepo
from xcopilot.llm.schemas import ChoiceA
from xcopilot.metrics.events import EventType, emit

RelationKind = Literal["same", "narrower", "broader", "contradicts", "different"]
PURPOSE = "brain.relation"
_LINK = {"narrower": "refines", "broader": "generalizes"}


@dataclass(frozen=True)
class Relation:
    kind: RelationKind
    confidence: float


@dataclass(frozen=True)
class Near:
    """Существующее знание рядом с кандидатом."""

    item_id: UUID
    statement: str
    similarity: float


async def nearest(
    ctx: BrainContext, vec: Sequence[float], k: int = 5, *, exclude: Sequence[UUID] = ()
) -> list[Near]:
    """До k рабочих знаний аккаунта ближе related_threshold, по убыванию сходства."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    rows = await repo.nearest(
        vec, limit=k, min_similarity=await repo.related_threshold(), exclude=exclude
    )
    return [Near(r["item_id"], r["statement"], float(r["similarity"])) for r in rows]


async def relate(ctx: BrainContext, statement: str, near: Near) -> Relation:
    """Отношение нового утверждения к существующему по судье (пороги из brain.toml)."""
    verdict = await ctx.llm.decide(
        state=f"1) {near.statement}\n2) {statement}",
        questions=judge_questions.RELATION,
        account_id=ctx.account_id,
        purpose=PURPOSE,
    )
    answer = verdict.answers.get("relation")
    if not isinstance(answer, ChoiceA):
        return Relation("different", 0.0)
    cfg = ctx.config.judge
    kind, conf = answer.choice, answer.confidence
    if kind == "same" and conf >= cfg.relation_same:
        return Relation("same", conf)
    if kind == "contradicts" and conf >= cfg.relation_contradicts:
        return Relation("contradicts", conf)
    if kind in _LINK:
        return Relation(cast(RelationKind, kind), conf)
    return Relation("different", conf)


async def _to_conflicted(ctx: BrainContext, item_id: UUID, link_id: UUID) -> None:
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).require(item_id)
    if item.status == "conflicted":
        return
    await lifecycle.apply(
        ctx,
        lifecycle.Transition(
            item_id=item_id,
            from_status=item.status,
            to_status="conflicted",
            rule="conflict_opened",
            reason="противоречит другому знанию",
            payload={"link_id": str(link_id)},
        ),
    )


async def apply_relation(
    ctx: BrainContext, new_item_id: UUID, near: Near, relation: Relation
) -> str:
    """Слияние, связь или конфликт; вернуть действие: merged, linked, conflict, none."""
    links = KnowledgeLinksRepo(ctx.session, ctx.account_id)
    now = ctx.clock.now()
    if relation.kind == "same":
        moved = await BrainRepo(ctx.session, ctx.account_id).move_evidence(
            new_item_id, near.item_id, at=now
        )
        await links.add(
            from_item_id=new_item_id,
            to_item_id=near.item_id,
            relation="duplicates",
            confidence=relation.confidence,
            decided_by="judge",
            status="resolved",
            resolution="kept_to",
            resolved_at=now,
        )
        await lifecycle.apply(
            ctx,
            lifecycle.Transition(
                item_id=new_item_id,
                from_status="candidate",
                to_status="merged",
                rule="merged",
                reason="то же, что существующее знание",
                payload={"into": str(near.item_id), "confidence": relation.confidence},
            ),
            merged_into=near.item_id,
        )
        await emit(
            ctx.session,
            account_id=ctx.account_id,
            type=EventType.KNOWLEDGE_MERGED,
            entity=("knowledge_item", new_item_id),
            props={
                "item_id": str(new_item_id),
                "into_item_id": str(near.item_id),
                "evidence_moved": moved,
            },
        )
        return "merged"
    if relation.kind in _LINK:
        await links.add(
            from_item_id=new_item_id,
            to_item_id=near.item_id,
            relation=_LINK[relation.kind],
            confidence=relation.confidence,
            decided_by="judge",
            status="resolved",
            resolution="both",
            resolved_at=now,
        )
        return "linked"
    if relation.kind == "contradicts":
        link = await links.add(
            from_item_id=new_item_id,
            to_item_id=near.item_id,
            relation="contradicts",
            confidence=relation.confidence,
            decided_by="judge",
        )
        for item_id in (new_item_id, near.item_id):
            await _to_conflicted(ctx, item_id, link.id)
        await emit(
            ctx.session,
            account_id=ctx.account_id,
            type=EventType.KNOWLEDGE_CONFLICT_OPENED,
            entity=("knowledge_link", link.id),
            props={
                "link_id": str(link.id),
                "from_item_id": str(new_item_id),
                "to_item_id": str(near.item_id),
            },
        )
        return "conflict"
    return "none"


async def resolve_conflicts(ctx: BrainContext) -> int:
    """Открытые противоречия с разрывом ≥ 3 своих доказательств решаются сами; вернуть число."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    links = KnowledgeLinksRepo(ctx.session, ctx.account_id)
    gap = ctx.config.thresholds.conflict_own_gap
    resolved = 0
    for row in await repo.open_conflicts():
        diff = int(row["from_net"]) - int(row["to_net"])
        if abs(diff) < gap:
            continue
        winner, loser = (
            (row["from_item_id"], row["to_item_id"])
            if diff > 0
            else (row["to_item_id"], row["from_item_id"])
        )
        await links.resolve(
            row["id"], resolution="kept_from" if diff > 0 else "kept_to", at=ctx.clock.now()
        )
        opened = await repo.conflict_transition(winner)
        if opened is not None:
            await lifecycle.revert(ctx, opened["id"], None)
        await lifecycle.apply(
            ctx,
            lifecycle.Transition(
                item_id=loser,
                from_status="conflicted",
                to_status="archived",
                rule="conflict_resolved",
                reason=f"проиграло противоречие: разрыв {abs(diff)} своих доказательств",
                payload={"link_id": str(row["id"]), "gap": abs(diff)},
            ),
        )
        resolved += 1
    return resolved
