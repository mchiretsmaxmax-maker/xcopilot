"""Доступ к знаниям для любой модели: пакет под задачу, поиск, карточка, объяснение (этап 9B).

Модели не нужен доступ к таблицам: всё, что знает база, выражено в `KnowledgePack`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from xcopilot.brain import rank, render
from xcopilot.brain.probation import BrainProbationPicker
from xcopilot.brain.rank import Candidate, Scored
from xcopilot.brain.schemas import (
    ExplainedItem,
    ItemCard,
    KnowledgePack,
    PackExplanation,
    PackItem,
    TaskSpec,
)
from xcopilot.clock import Clock
from xcopilot.db.repos.knowledge import KnowledgePacksRepo
from xcopilot.db.repos.pack import PackRepo
from xcopilot.db.repos.writer import WriterRepo
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.errors import NotFound
from xcopilot.metrics.events import EventType, emit
from xcopilot.retrieval.tokens import estimate_tokens
from xcopilot.writer.probation import ProbationPicker

PACK_SCHEMA_VERSION = 1
_MAX_REFILL = 8


def _facets(task: TaskSpec) -> dict[str, str]:
    facets = {"format": task.format, "goal": task.goal, "lang": task.lang}
    if task.niche:
        facets["niche"] = task.niche
    if task.archetype:
        facets["archetype"] = task.archetype
    return facets


def _candidate(row: Mapping[str, Any]) -> Candidate:
    return Candidate(
        item_id=row["id"],
        version=row["version"],
        kind=row["kind"],
        layer=row["layer"],
        element=row["element"],
        statement=row["statement"],
        guidance=row["guidance"],
        skeleton=row["skeleton"],
        status=row["status"],
        owner_state=row["owner_state"],
        quality=float(row["quality"]),
        scopes={k: frozenset(v) for k, v in (row.get("scopes") or {}).items()},
        vec=row.get("vec"),
        last_evidence_at=row["last_evidence_at"],
        own_support=int(row["own_support"]),
        own_contra=int(row["own_contra"]),
        probation_started_at=row["probation_started_at"],
    )


def _without_duplicates(scored: list[Scored], pairs: set[frozenset[UUID]]) -> list[Scored]:
    """Из пары дубликатов остаётся лучшее по оценке."""
    kept: list[Scored] = []
    for s in sorted(scored, key=lambda x: (-x.score, str(x.item.item_id))):
        if any(frozenset((s.item.item_id, k.item.item_id)) in pairs for k in kept):
            continue
        kept.append(s)
    return kept


async def build_pack(
    session: DbSession,
    account_id: UUID,
    task: TaskSpec,
    *,
    embedder: Embedder,
    clock: Clock,
    consumer: str = "writer",
    probation: ProbationPicker | None = None,
    record: bool = True,
    with_probation: bool | None = None,
) -> KnowledgePack:
    """Пакет под задачу: допущенные → область → оценка → слоты/MMR → бюджет → запись выдачи."""
    now = clock.now()
    repo = PackRepo(session, account_id)
    topic_vec = (await asyncio.to_thread(embedder.embed, [task.topic]))[0]
    rows = await repo.candidates(_facets(task), topic_vec)
    cands = [_candidate(r) for r in rows]
    scored = [
        Scored(c, rank.score(c, task, topic_vec, now))
        for c in cands
        if rank.scope_match(c, task) is not None
    ]
    scored = _without_duplicates(scored, await repo.duplicate_pairs([c.item_id for c in cands]))
    foreign = await repo.foreign_texts([s.item.item_id for s in scored])

    def clean(s: Scored) -> bool:
        texts = foreign.get(s.item.item_id)
        return not texts or not any(
            rank.shares_run(t, texts)
            for t in (s.item.guidance, s.item.statement, s.item.skeleton or "")
        )

    scored = [s for s in scored if clean(s)]
    slots = rank.slots_for(task.format)
    want_probation = task.include_probation if with_probation is None else with_probation
    trial: PackItem | None = None
    if want_probation:
        picked = await (probation or BrainProbationPicker(clock)).pick(session, account_id, task)
        if picked is not None:
            cards = await repo.cards([picked.item_id])
            if cards:
                trial = _trial_item(cards[0])
    taxonomy = await WriterRepo(session, account_id).taxonomy_version()

    budget = task.budget_tokens
    items: list[PackItem] = []
    pack = None
    for _ in range(_MAX_REFILL):
        items = rank.fill_slots(scored, slots, budget)
        listed = _with_trial(items, trial)
        pack = KnowledgePack(
            pack_id=uuid4(),
            account_id=account_id,
            task=task,
            taxonomy_version=taxonomy,
            items=listed,
            token_count=0,
            built_at=now,
        )
        tokens = estimate_tokens(render.to_markdown(pack)) if listed else 0
        if tokens <= task.budget_tokens:
            break
        if not items:
            trial = None
            continue
        budget = max(0, budget - (tokens - task.budget_tokens))
    assert pack is not None
    pack = pack.model_copy(update={"token_count": tokens if pack.items else 0})
    if not pack.items:
        warn = "no_match" if await repo.has_usable() else "empty_base"
        pack = pack.model_copy(update={"warnings": [warn]})
    if record:
        pack_id = await _record(session, account_id, pack, consumer)
        pack = pack.model_copy(update={"pack_id": pack_id})
    return pack


def _trial_item(card: Mapping[str, Any]) -> PackItem:
    return PackItem.model_validate(
        {
            "item_id": card["id"],
            "version": card["version"],
            "slot": "probation",
            "rank": 1,
            "kind": card["kind"],
            "layer": card["layer"],
            "element": card["element"],
            "guidance": card["guidance"],
            "statement": card["statement"],
            "skeleton": card["skeleton"],
            "confidence": card["confidence_label"],
            "proven_on_own_posts": False,
            "score": 0.0,
        }
    )


def _with_trial(items: list[PackItem], trial: PackItem | None) -> list[PackItem]:
    if trial is None or any(i.item_id == trial.item_id for i in items):
        return items
    return [*items, trial.model_copy(update={"rank": len(items) + 1})]


def pack_rows(items: list[PackItem]) -> list[dict[str, Any]]:
    return [
        {
            "item_id": str(i.item_id),
            "version": i.version,
            "slot": i.slot,
            "rank": i.rank,
            "score": i.score,
            "confidence": i.confidence,
            "proven": i.proven_on_own_posts,
        }
        for i in items
    ]


async def _record(session: DbSession, account_id: UUID, pack: KnowledgePack, consumer: str) -> UUID:
    row = await KnowledgePacksRepo(session, account_id).add(
        consumer=consumer,
        task=pack.task.model_dump(mode="json"),
        schema_version=PACK_SCHEMA_VERSION,
        taxonomy_version=pack.taxonomy_version,
        items=pack_rows(pack.items),
        token_count=pack.token_count,
    )
    await emit(
        session,
        account_id=account_id,
        type=EventType.KNOWLEDGE_PACK_BUILT,
        entity=("knowledge_pack", row.id),
        props={
            "pack_id": str(row.id),
            "consumer": consumer,
            "items": len(pack.items),
            "token_count": pack.token_count,
            "format": pack.task.format,
        },
    )
    return row.id


async def load_pack(session: DbSession, account_id: UUID, pack_id: UUID) -> KnowledgePack:
    """Пакет, восстановленный по сохранённым id и версиям (текст — как на момент выдачи)."""
    repo = PackRepo(session, account_id)
    row = await repo.pack(pack_id)
    if row is None:
        raise NotFound(f"knowledge_packs: {pack_id} не найден")
    stored = list(row["items"])
    texts = await repo.versions([(UUID(r["item_id"]), r.get("version")) for r in stored])
    items: list[PackItem] = []
    for r in stored:
        t = texts.get(UUID(r["item_id"]))
        if t is None:
            continue
        items.append(
            PackItem.model_validate(
                {
                    "item_id": r["item_id"],
                    "version": t["version"],
                    "slot": r["slot"],
                    "rank": r["rank"],
                    "kind": t["kind"],
                    "layer": t["layer"],
                    "element": t["element"],
                    "guidance": t["guidance"],
                    "statement": t["statement"],
                    "skeleton": t["skeleton"],
                    "confidence": r.get("confidence", "medium"),
                    "proven_on_own_posts": bool(r.get("proven", False)),
                    "score": r.get("score") or 0.0,
                }
            )
        )
    return KnowledgePack(
        pack_id=pack_id,
        account_id=account_id,
        task=TaskSpec.model_validate(row["task"]),
        taxonomy_version=row["taxonomy_version"],
        items=items,
        token_count=row["token_count"],
        built_at=row["created_at"],
    )


async def search(
    session: DbSession,
    account_id: UUID,
    query: str,
    *,
    embedder: Embedder,
    filters: Mapping[str, Any] | None = None,
    k: int = 10,
) -> list[PackItem]:
    vec = (await asyncio.to_thread(embedder.embed, [query]))[0]
    rows = await PackRepo(session, account_id).search(vec, dict(filters or {}), k=k)
    out: list[PackItem] = []
    for n, r in enumerate(rows, start=1):
        c = _candidate({**r, "scopes": {}})
        out.append(
            rank.to_pack_item(
                Scored(c, max(0.0, float(r["sim"]))),
                "probation"
                if c.status == "probation"
                else "anti"
                if c.kind == "anti_pattern"
                else "core",
                n,
            )
        )
    return out


def card_of(row: Mapping[str, Any]) -> ItemCard:
    return ItemCard(
        item_id=row["id"],
        version=row["version"],
        kind=row["kind"],
        layer=row["layer"],
        element=row["element"],
        statement=row["statement"],
        guidance=row["guidance"],
        skeleton=row["skeleton"],
        status=row["status"],
        owner_state=row["owner_state"],
        confidence=row["confidence_label"],
        scopes={k: list(v) for k, v in (row["scopes"] or {}).items()},
        evidence_n=row["evidence_n"],
        own_support=row["own_support"],
        own_contra=row["own_contra"],
        open_conflicts=row["open_conflicts"],
    )


async def get_item(session: DbSession, account_id: UUID, item_id: UUID) -> ItemCard:
    rows = await PackRepo(session, account_id).cards([item_id])
    if not rows:
        raise NotFound(f"knowledge_items: {item_id} не найден")
    return card_of(rows[0])


async def explain(session: DbSession, account_id: UUID, pack_id: UUID) -> PackExplanation:
    repo = PackRepo(session, account_id)
    row = await repo.pack(pack_id)
    if row is None:
        raise NotFound(f"knowledge_packs: {pack_id} не найден")
    stored = list(row["items"])
    texts = await repo.versions([(UUID(r["item_id"]), r.get("version")) for r in stored])
    return PackExplanation(
        pack_id=pack_id,
        consumer=row["consumer"],
        task=TaskSpec.model_validate(row["task"]),
        taxonomy_version=row["taxonomy_version"],
        token_count=row["token_count"],
        items=[
            ExplainedItem(
                item_id=UUID(r["item_id"]),
                version=r.get("version"),
                slot=r["slot"],
                rank=r["rank"],
                score=r.get("score"),
                statement=(texts.get(UUID(r["item_id"])) or {}).get("statement"),
            )
            for r in stored
        ],
    )
