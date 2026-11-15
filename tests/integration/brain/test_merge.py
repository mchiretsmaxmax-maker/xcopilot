"""Проверка кандидата и слияние (этап 9A, задачи 5–6): судья, слой, дубли, связи, конфликты."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_stand import (
    NOW,
    FakeBrainLLM,
    candidate_judge,
    knowledge,
    make_ctx,
    material,
    new_account,
    new_author,
)
from tests.integration.brain.extract_helpers import candidate, count, items_of, result
from tests.integration.db.brain_helpers import evidence as raw_evidence
from tests.integration.db.brain_helpers import source as raw_source
from xcopilot.brain import extract
from xcopilot.brain.merge import Near, Relation, apply_relation, resolve_conflicts
from xcopilot.brain.scoring import recompute
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo, KnowledgeLinksRepo

STATEMENT = "Личная цифра в первой строке даёт больше ответов"


async def run(session: AsyncSession, acc: UUID, llm: FakeBrainLLM) -> list[UUID]:
    srcs = [
        await material(session, acc, author_id=await new_author(session, acc)) for _ in range(3)
    ]
    ctx = make_ctx(session, acc, llm=llm)
    batch = extract.Batch(account_id=acc, format="post", materials=srcs)
    return await extract.ingest_candidates(ctx, await extract.extract(ctx, batch), batch)


async def test_not_craft_claim_dropped(session: AsyncSession) -> None:
    acc = await new_account(session)
    llm = FakeBrainLLM(judge=candidate_judge(craft=0.6)).push(result(candidate()))
    assert await run(session, acc, llm) == []
    assert await items_of(session, acc) == []
    acc2 = await new_account(session)
    llm2 = FakeBrainLLM(judge=candidate_judge(craft=0.9, portable=0.4)).push(result(candidate()))
    assert await run(session, acc2, llm2) == []


async def test_judge_layer_overrides_when_confident(session: AsyncSession) -> None:
    acc = await new_account(session)
    llm = FakeBrainLLM(judge=candidate_judge(layer="platform", layer_conf=0.8)).push(
        result(candidate())
    )
    [iid] = await run(session, acc, llm)
    [row] = await items_of(session, acc)
    assert (row["id"], row["layer"], row["risk_class"]) == (iid, "platform", "B")
    acc2 = await new_account(session)
    llm2 = FakeBrainLLM(judge=candidate_judge(layer="platform", layer_conf=0.6)).push(
        result(candidate())
    )
    await run(session, acc2, llm2)
    [row2] = await items_of(session, acc2)
    assert row2["layer"] == "craft"


async def test_duplicate_merges_into_older_item(session: AsyncSession) -> None:
    acc = await new_account(session)
    old = await knowledge(
        session,
        acc,
        statement=STATEMENT,
        guidance="Начни пост с личной цифры",
        status="active",
        embed=True,
    )
    llm = FakeBrainLLM(judge=candidate_judge(relation="same", relation_conf=0.9)).push(
        result(candidate())
    )
    touched = await run(session, acc, llm)
    assert old in touched
    rows = await items_of(session, acc)
    new = next(r for r in rows if r["id"] != old)
    assert (new["status"], new["merged_into"]) == ("merged", old)
    assert (
        await count(session, "SELECT count(*) FROM knowledge_evidence WHERE item_id = :i", i=old)
        == 2
    )
    assert (
        await count(
            session,
            "SELECT count(*) FROM knowledge_evidence WHERE item_id = :i AND revoked_at IS NULL",
            i=new["id"],
        )
        == 0
    )
    assert (
        await count(
            session,
            "SELECT count(*) FROM events WHERE account_id = :a AND type = 'knowledge.merged'",
            a=acc,
        )
        == 1
    )


async def test_narrower_creates_refines_link(session: AsyncSession) -> None:
    acc = await new_account(session)
    old = await knowledge(
        session,
        acc,
        statement=STATEMENT,
        guidance="Начни пост с личной цифры",
        status="active",
        embed=True,
    )
    llm = FakeBrainLLM(judge=candidate_judge(relation="narrower", relation_conf=0.8)).push(
        result(candidate())
    )
    touched = await run(session, acc, llm)
    new = next(i for i in touched if i != old)
    links = await KnowledgeLinksRepo(session, acc).list()
    assert [(link.from_item_id, link.to_item_id, link.relation) for link in links] == [
        (new, old, "refines")
    ]
    assert (await KnowledgeItemsRepo(session, acc).require(new)).status == "candidate"


async def test_contradiction_opens_conflict_and_hides_both(session: AsyncSession) -> None:
    acc = await new_account(session)
    old = await knowledge(
        session,
        acc,
        statement=STATEMENT,
        guidance="Начни пост с личной цифры",
        status="active",
        embed=True,
    )
    llm = FakeBrainLLM(judge=candidate_judge(relation="contradicts", relation_conf=0.8)).push(
        result(candidate())
    )
    touched = await run(session, acc, llm)
    new = next(i for i in touched if i != old)
    [link] = await KnowledgeLinksRepo(session, acc).open_conflicts()
    assert {link.from_item_id, link.to_item_id} == {new, old}
    repo = KnowledgeItemsRepo(session, acc)
    assert (await repo.require(old)).status == "conflicted"
    assert (await repo.require(new)).status == "conflicted"
    usable = {i.id for i in await repo.usable()}
    assert old not in usable and new not in usable
    assert (
        await count(
            session,
            "SELECT count(*) FROM events WHERE account_id = :a"
            " AND type = 'knowledge.conflict_opened'",
            a=acc,
        )
        == 1
    )


async def test_conflict_auto_resolved_by_own_evidence_gap_3(session: AsyncSession) -> None:
    acc = await new_account(session)
    a = await knowledge(session, acc, statement=STATEMENT, status="active")
    b = await knowledge(
        session, acc, statement="Общая фраза в первой строке даёт больше ответов", status="active"
    )
    ctx = make_ctx(session, acc)
    await apply_relation(
        ctx, a, Near(item_id=b, statement="", similarity=0.9), Relation("contradicts", 0.8)
    )
    # разрыв 2 — ещё не решается
    for _ in range(2):
        sid = await raw_source(session, acc, stance="own")
        await raw_evidence(
            session,
            acc,
            a,
            observed_at=NOW - timedelta(days=1),
            source_id=sid,
            basis="own_outcome",
            w_scope=2.0,
        )
    await recompute(ctx)
    assert await resolve_conflicts(ctx) == 0
    sid = await raw_source(session, acc, stance="own")
    await raw_evidence(
        session,
        acc,
        a,
        observed_at=NOW - timedelta(days=1),
        source_id=sid,
        basis="own_outcome",
        w_scope=2.0,
    )
    await recompute(ctx)
    assert await resolve_conflicts(ctx) == 1
    assert await KnowledgeLinksRepo(session, acc).open_conflicts() == []
    repo = KnowledgeItemsRepo(session, acc)
    assert (await repo.require(a)).status == "active"
    assert (await repo.require(b)).status == "archived"
