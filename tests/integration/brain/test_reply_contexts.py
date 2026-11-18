"""Независимость доказательств по контексту (ADR 0034, п. 6; этап R2 самообучения на ответах).

Ответы под одним постом — один контекст: `context_key` = id исходного поста (`meta.harvest`),
у прочих материалов ключа нет (каждый материал — свой контекст). Скидка `w_author` берёт
меньшее из 1/k по автору и 1/k по контексту; извлечению и испытанию нужны ≥ 2 контекста.
"""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import (
    NOW,
    FakeBrainLLM,
    candidate_judge,
    knowledge,
    make_ctx,
    material,
    new_account,
    new_author,
)
from tests.fakes import FakeClock
from tests.integration.brain.extract_helpers import candidate, items_of, result
from tests.integration.db.brain_helpers import author, evidence, source
from xcopilot.brain import extract, lifecycle
from xcopilot.brain.evidence import record_from_material
from xcopilot.brain.scoring import recompute


async def harvested(
    session: AsyncSession,
    acc: UUID,
    post_id: str,
    *,
    author_id: UUID | None = None,
    posted_at: datetime = NOW,
    **extra: Any,
) -> UUID:
    """Материал сбора: ответ под постом `post_id` (`origin = reply_harvest`, `meta.harvest`)."""
    auth = author_id or await new_author(session, acc)
    sid = await material(session, acc, kind="reply", author_id=auth, posted_at=posted_at, **extra)
    await session.execute(
        text("UPDATE sources SET origin = 'reply_harvest', meta = CAST(:m AS jsonb) WHERE id = :s"),
        {"m": json.dumps({"harvest": post_id, "silent": True}), "s": sid},
    )
    return sid


async def ev_rows(session: AsyncSession, item_id: UUID) -> list[dict[str, Any]]:
    res = await session.execute(
        text(
            "SELECT source_id, context_key, w_author::float FROM knowledge_evidence"
            " WHERE item_id = :i AND revoked_at IS NULL ORDER BY created_at, id"
        ),
        {"i": item_id},
    )
    return [dict(r._mapping) for r in res.all()]


async def reply_item(session: AsyncSession, acc: UUID) -> UUID:
    return await knowledge(session, acc, element="hook")


# 4
async def test_evidence_context_key_from_harvest_meta(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await reply_item(session, acc)
    ctx = make_ctx(session, acc)
    reply = await harvested(session, acc, "123")
    plain = await material(session, acc, author_id=await new_author(session, acc))
    assert await record_from_material(ctx, reply) == 1
    assert await record_from_material(ctx, plain) == 1
    keys = {r["source_id"]: r["context_key"] for r in await ev_rows(session, item)}
    assert keys == {reply: "123", plain: None}


# 5
async def test_w_author_discounts_same_context(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await reply_item(session, acc)
    ctx = make_ctx(session, acc)
    for _ in range(3):
        assert await record_from_material(ctx, await harvested(session, acc, "777")) == 1
    w = [round(r["w_author"], 3) for r in await ev_rows(session, item)]
    assert w == [1.0, 0.5, 0.333]


# 6
async def test_w_author_takes_min_of_author_and_context(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await reply_item(session, acc)
    ctx = make_ctx(session, acc)
    a = await new_author(session, acc)
    b = await new_author(session, acc)
    await record_from_material(ctx, await harvested(session, acc, "X", author_id=a))
    # тот же автор в новом контексте: 1/k_author = 0.5, 1/k_context = 1 → 0.5
    await record_from_material(ctx, await harvested(session, acc, "Y", author_id=a))
    # новый автор в том же контексте Y: 1/k_author = 1, 1/k_context = 0.5 → 0.5
    await record_from_material(ctx, await harvested(session, acc, "Y", author_id=b))
    w = [round(r["w_author"], 4) for r in await ev_rows(session, item)]
    assert w == [1.0, 0.5, 0.5]


# 7
async def test_own_material_not_discounted(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await reply_item(session, acc)
    me = await new_author(session, acc, is_self=True)
    ctx = make_ctx(session, acc)
    for _ in range(3):
        sid = await harvested(
            session,
            acc,
            "555",
            author_id=me,
            stance="own",
            signal_origin="own_sync",
            outcome_basis="own_baseline",
        )
        assert await record_from_material(ctx, sid) == 1
    assert [r["w_author"] for r in await ev_rows(session, item)] == [1.0, 1.0, 1.0]


# 8
async def test_recompute_counts_independent_contexts(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    for i in range(4):
        auth = await author(session, acc, f"ctx{i}_{iid.hex[:6]}")
        sid = await source(session, acc, origin="screenshot", author_id=auth)
        await evidence(
            session, acc, iid, observed_at=NOW, source_id=sid, author_id=auth, context_key="A"
        )
    auth = await author(session, acc, f"solo_{iid.hex[:6]}")
    sid = await source(session, acc, origin="screenshot", author_id=auth)
    await evidence(session, acc, iid, observed_at=NOW, source_id=sid, author_id=auth)
    await recompute(make_ctx(session, acc), now=NOW)
    row = (
        await session.execute(
            text("SELECT independent_contexts, independent_authors FROM knowledge_items"
                 " WHERE id = :i"),
            {"i": iid},
        )
    ).one()  # fmt: skip
    assert (row.independent_contexts, row.independent_authors) == (2, 5)


# 9
async def test_extract_rejects_single_context_support(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(2):
        await harvested(session, acc, "900", posted_at=NOW - timedelta(hours=1))
    llm = FakeBrainLLM(judge=candidate_judge()).push(result(candidate(supporting=["M1", "M2"])))
    clock = FakeClock(datetime.now(UTC))
    report = await extract.run_report(make_ctx(session, acc, llm=llm, clock=clock), force=True)
    assert report.added == []
    assert await items_of(session, acc) == []
    assert report.rejected.get("single_context") == 1


# 10
async def test_extract_accepts_two_contexts(session: AsyncSession) -> None:
    acc = await new_account(session)
    await harvested(session, acc, "901", posted_at=NOW - timedelta(hours=1))
    await harvested(session, acc, "902", posted_at=NOW - timedelta(hours=1))
    llm = FakeBrainLLM(judge=candidate_judge()).push(result(candidate(supporting=["M1", "M2"])))
    clock = FakeClock(datetime.now(UTC))
    report = await extract.run_report(make_ctx(session, acc, llm=llm, clock=clock), force=True)
    assert len(report.added) == 1
    assert "single_context" not in report.rejected
    [row] = await items_of(session, acc)
    assert row["id"] == report.added[0]


# 11
async def test_batch_caps_per_context(session: AsyncSession) -> None:
    acc = await new_account(session)
    context: dict[UUID, str] = {}
    for _ in range(30):
        context[await harvested(session, acc, "A")] = "A"
    for key in ("B", "C"):
        for _ in range(5):
            context[await harvested(session, acc, key)] = key
    clock = FakeClock(datetime.now(UTC))
    batches = await extract.build_batches(make_ctx(session, acc, clock=clock))
    assert batches
    for b in batches:
        assert max(Counter(context[m] for m in b.materials).values()) <= 8
    first = {context[m] for m in batches[0].materials}
    assert first == {"A", "B", "C"}


# 12
async def test_harvest_batch_waits_for_three_contexts(session: AsyncSession) -> None:
    acc = await new_account(session)
    for key in ("P", "Q"):
        for _ in range(6):
            await harvested(session, acc, key)
    clock = FakeClock(datetime.now(UTC))
    assert await extract.build_batches(make_ctx(session, acc, clock=clock)) == []
    clock.advance(timedelta(days=1, hours=1).total_seconds())
    later = await extract.build_batches(make_ctx(session, acc, clock=clock))
    assert sum(len(b.materials) for b in later) > 0


# 13
async def test_probation_needs_two_contexts(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc, provenance={"voice_compat": 0.8})
    for i in range(3):
        auth = await author(session, acc, f"one{i}_{iid.hex[:6]}")
        sid = await source(session, acc, origin="screenshot", author_id=auth)
        await evidence(
            session,
            acc,
            iid,
            observed_at=NOW,
            source_id=sid,
            author_id=auth,
            w_source=0.7,
            context_key="post-1",
        )
    ctx = make_ctx(session, acc)
    await recompute(ctx, now=NOW)
    assert await lifecycle.run(ctx, NOW) == []
    status = (
        await session.execute(text("SELECT status FROM knowledge_items WHERE id = :i"), {"i": iid})
    ).scalar_one()
    assert status == "candidate"
    auth = await author(session, acc, f"two_{iid.hex[:6]}")
    sid = await source(session, acc, origin="screenshot", author_id=auth)
    await evidence(
        session,
        acc,
        iid,
        observed_at=NOW,
        source_id=sid,
        author_id=auth,
        w_source=0.7,
        context_key="post-2",
    )
    await recompute(ctx, now=NOW)
    [t] = await lifecycle.run(ctx, NOW)
    assert (t.item_id, t.to_status, t.rule) == (iid, "probation", "auto_probation")
