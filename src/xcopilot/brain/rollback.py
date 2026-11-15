"""Откат базы знаний на дату и отмена отката (ТЗ v3.1, раздел 2.6).

Откат на момент T одной транзакцией: доказательства, записанные после T, отзываются с причиной
`rollback:<id>`; знания, созданные после T, уходят в archived; остальным возвращается статус до
первого перехода после T (и решение владельца из снимка на дату T), а текст — новой версией,
повторяющей версию на T; веса пересчитываются; пишется событие `knowledge.rollback`.
Материалы и разборы не трогаются.

Отдельной таблицы откатов нет: id отката — в причине отзыва, в `payload.rollback_id` переходов и
в `provenance.rollback_id` версий текста (ADR 0019). Отмена отката снимает отзыв, откатывает
эти переходы и возвращает текст, бывший до отката.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from xcopilot.brain import lifecycle
from xcopilot.brain.context import BrainContext
from xcopilot.brain.scoring import recompute
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.knowledge import VERSIONED_FIELDS, KnowledgeItemsRepo
from xcopilot.metrics.events import EventType, emit


@dataclass(frozen=True)
class RollbackPlan:
    account_id: UUID
    to_ts: datetime
    created_items: list[UUID] = field(default_factory=list)
    changed_items: list[UUID] = field(default_factory=list)
    evidence_added: int = 0


def reason_for(rollback_id: UUID) -> str:
    return f"rollback:{rollback_id}"


async def plan(ctx: BrainContext, to_ts: datetime) -> RollbackPlan:
    """Предпросмотр: что создано, изменено и добавлено после to_ts."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    created = await repo.created_after(to_ts)
    changed: list[UUID] = []
    for row in [*await repo.transitions_after(to_ts), *await repo.versions_after(to_ts)]:
        item_id = row["item_id"]
        if item_id not in created and item_id not in changed:
            changed.append(item_id)
    return RollbackPlan(
        account_id=ctx.account_id,
        to_ts=to_ts,
        created_items=created,
        changed_items=changed,
        evidence_added=await repo.count_live_evidence_after(to_ts),
    )


def _text_of(version: dict[str, Any]) -> dict[str, Any]:
    return {f: version[f] for f in VERSIONED_FIELDS}


async def execute(ctx: BrainContext, rollback: RollbackPlan, user_id: UUID | None) -> UUID:
    """Откат одной транзакцией; вернуть id отката (причина отзыва — rollback:<id>)."""
    if rollback.account_id != ctx.account_id:
        raise ValueError("rollback: план другого аккаунта")
    rid = uuid4()
    repo = BrainRepo(ctx.session, ctx.account_id)
    items = KnowledgeItemsRepo(ctx.session, ctx.account_id)
    t = rollback.to_ts
    revoked = await repo.revoke_evidence(
        at=ctx.clock.now(), reason=reason_for(rid), created_after=t
    )
    payload = {"rollback_id": str(rid), "to_ts": t.isoformat()}
    transitions = 0
    for item_id in rollback.created_items:
        item = await items.require(item_id)
        if item.status != "archived":
            await lifecycle.apply(
                ctx,
                lifecycle.Transition(
                    item_id,
                    item.status,
                    "archived",
                    "rollback",
                    "откат: создано после даты",
                    payload,
                ),
                user_id=user_id,
            )
            transitions += 1
    first_after: dict[UUID, str | None] = {}
    for row in await repo.transitions_after(t):
        first_after.setdefault(row["item_id"], row["from_status"])
    owner_states = await repo.snapshot_owner_state(t.date())
    for item_id in rollback.changed_items:
        item = await items.require(item_id)
        target = first_after.get(item_id) or item.status
        owner_before = owner_states.get(item_id)
        if target != item.status or (owner_before and owner_before != item.owner_state):
            await lifecycle.apply(
                ctx,
                lifecycle.Transition(
                    item_id,
                    item.status,
                    target,
                    "rollback",
                    "откат: состояние на дату",
                    payload | {"owner_state_before": item.owner_state},
                ),
                user_id=user_id,
            )
            transitions += 1
            if owner_before and owner_before != item.owner_state:
                await repo.set_owner_state(item_id, owner_before)
        version = await repo.version_at(item_id, t)
        if version is not None and version["version"] != item.version:
            await items.edit(
                item_id,
                changed_by="owner" if user_id else "system",
                user_id=user_id,
                reason="rollback",
                provenance={"rollback_id": str(rid), "from_version": item.version},
                **_text_of(version),
            )
    await recompute(ctx)
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.KNOWLEDGE_ROLLBACK,
        actor="user" if user_id else "system",
        user_id=user_id,
        entity=("knowledge_rollback", rid),
        props={
            "to_date": t.date().isoformat(),
            "transitions_reverted": transitions,
            "evidence_revoked": len(revoked),
        },
    )
    return rid


async def undo(ctx: BrainContext, rollback_id: UUID, user_id: UUID | None) -> None:
    """Отменить откат: вернуть доказательства, отменить переходы, восстановить тексты."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    items = KnowledgeItemsRepo(ctx.session, ctx.account_id)
    await repo.unrevoke_evidence(reason_for(rollback_id))
    for tid in await repo.rollback_transitions(rollback_id):
        row = await repo.transition(tid)
        await lifecycle.revert(ctx, tid, user_id)
        owner_before = (row or {}).get("payload", {}).get("owner_state_before")
        if row is not None and owner_before:
            await repo.set_owner_state(row["item_id"], owner_before)
    for v in await repo.rollback_versions(rollback_id):
        rolled = await repo.version(v["item_id"], v["version"])
        assert rolled is not None
        before = await repo.version(v["item_id"], int(rolled["provenance"]["from_version"]))
        if before is None:
            continue
        await items.edit(
            v["item_id"],
            changed_by="owner" if user_id else "system",
            user_id=user_id,
            reason="rollback",
            provenance={"undo_of": str(rollback_id), "from_version": rolled["version"]},
            **_text_of(before),
        )
    await recompute(ctx)
