"""Доказательства из материалов (этап 9A, задача 3): наличие × исход → строка с множителями."""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_stand import NOW, knowledge, make_ctx, material, new_account, new_author
from tests.integration.db.brain_helpers import evidence as raw_evidence
from xcopilot.brain.evidence import record_from_material, record_own_outcome


async def rows(session: AsyncSession, item_id: UUID) -> list[dict[str, Any]]:
    res = await session.execute(
        text(
            "SELECT source_id, dimension, basis, polarity, outcome_tier, target_metric,"
            " w_source::float, w_scope::float, w_magnitude::float, w_author::float"
            " FROM knowledge_evidence WHERE item_id = :i AND revoked_at IS NULL ORDER BY id"
        ),
        {"i": item_id},
    )
    return [dict(r._mapping) for r in res.all()]


async def setup(session: AsyncSession, tier: str) -> tuple[UUID, UUID, UUID]:
    acc = await new_account(session, niche="crypto")
    item = await knowledge(session, acc)
    auth = await new_author(session, acc, niche="crypto")
    sid = await material(session, acc, author_id=auth, tier=tier)
    n = await record_from_material(make_ctx(session, acc), sid)
    assert n == (0 if tier == "unknown" else 1)
    return acc, item, sid


async def test_present_and_hit_gives_support(session: AsyncSession) -> None:
    _, item, sid = await setup(session, "hit")
    [row] = await rows(session, item)
    assert row["source_id"] == sid
    assert (row["dimension"], row["basis"], row["polarity"]) == ("eff", "observed_outcome", 1)
    assert (row["outcome_tier"], row["target_metric"]) == ("hit", "engagement")
    assert (row["w_source"], row["w_scope"], row["w_magnitude"], row["w_author"]) == (
        0.7,
        1.0,
        1.0,
        1.0,
    )


async def test_present_and_flop_gives_contra(session: AsyncSession) -> None:
    _, item, _ = await setup(session, "flop")
    [row] = await rows(session, item)
    assert (row["polarity"], row["w_magnitude"]) == (-1, 1.0)


async def test_present_and_normal_gives_neutral(session: AsyncSession) -> None:
    _, item, _ = await setup(session, "normal")
    [row] = await rows(session, item)
    assert (row["polarity"], row["w_magnitude"]) == (0, 0.0)


async def test_unknown_outcome_gives_no_eff_evidence(session: AsyncSession) -> None:
    _, item, _ = await setup(session, "unknown")
    assert await rows(session, item) == []


async def test_weight_components_by_origin_and_niche(session: AsyncSession) -> None:
    acc = await new_account(session, niche="crypto")
    item = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    cases = [
        # (подпись, база исхода, ниша автора, ступень) → (основание, w_source, w_scope, w_mag)
        (("x_api", "rates", "crypto", "hit"), ("observed_outcome", 0.8, 1.0, 1.0)),
        (("screenshot", "rates", None, "above"), ("observed_outcome", 0.7, 0.8, 0.5)),
        (("manual_numbers", "rates", "fitness", "below"), ("observed_outcome", 0.6, 0.5, 0.5)),
        (("owner_label", "owner_label", "crypto", "hit"), ("observed_outcome", 0.4, 1.0, 1.0)),
        (("owner_pick", "owner_pick", "crypto", "hit"), ("owner_pick", 0.25, 1.0, 1.0)),
    ]
    for (origin, basis, niche, tier), _ in cases:
        auth = await new_author(session, acc, niche=niche)
        sid = await material(
            session, acc, author_id=auth, tier=tier, signal_origin=origin, outcome_basis=basis
        )
        assert await record_from_material(ctx, sid) == 1
    got = [
        (r["basis"], r["w_source"], r["w_scope"], r["w_magnitude"])
        for r in await rows(session, item)
    ]
    assert got == [expected for _, expected in cases]


async def test_same_author_discount_is_one_over_k(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await knowledge(session, acc)
    auth = await new_author(session, acc)
    ctx = make_ctx(session, acc)
    for _ in range(3):
        await record_from_material(ctx, await material(session, acc, author_id=auth))
    other = await new_author(session, acc)
    await record_from_material(ctx, await material(session, acc, author_id=other))
    w = [round(r["w_author"], 4) for r in await rows(session, item)]
    assert w == [1.0, 0.5, 0.3333, 1.0]


async def test_own_posts_not_discounted(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await knowledge(session, acc)
    me = await new_author(session, acc, is_self=True)
    ctx = make_ctx(session, acc)
    for _ in range(3):
        sid = await material(
            session,
            acc,
            stance="own",
            origin="x_api",
            author_id=me,
            signal_origin="own_sync",
            outcome_basis="own_baseline",
        )
        assert await record_own_outcome(ctx, sid) == 1
    got = [
        (r["basis"], r["w_source"], r["w_scope"], r["w_author"]) for r in await rows(session, item)
    ]
    assert got == [("own_outcome", 1.0, 2.0, 1.0)] * 3


async def test_evidence_unique_per_item_source_basis(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await knowledge(session, acc)
    sid = await material(session, acc, author_id=await new_author(session, acc))
    ctx = make_ctx(session, acc)
    assert await record_from_material(ctx, sid) == 1
    assert await record_from_material(ctx, sid) == 0
    assert len(await rows(session, item)) == 1


async def test_voice_item_rejects_foreign_evidence(session: AsyncSession) -> None:
    acc = await new_account(session)
    voice = await knowledge(
        session,
        acc,
        layer="voice",
        kind="thesis",
        element="tone",
        target_metric="quality",
        risk_class="A",
    )
    craft = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    foreign = await material(session, acc, author_id=await new_author(session, acc))
    await record_from_material(ctx, foreign)
    assert await rows(session, voice) == []
    assert len(await rows(session, craft)) == 1
    # триггер БД: чужое доказательство к знанию голоса не записывается даже напрямую
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await raw_evidence(session, acc, voice, observed_at=NOW, source_id=foreign)


async def test_declared_claim_weight_03(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await knowledge(session, acc)
    sid = await material(
        session,
        acc,
        kind="article",
        origin="url_fetch",
        author_id=await new_author(session, acc),
        tier="unknown",
        declared_claims=[
            {"statement": "Цифра в первой строке поднимает ответы", "quote": "12 провалов за год"}
        ],
    )
    assert await record_from_material(make_ctx(session, acc), sid) == 1
    [row] = await rows(session, item)
    assert (row["basis"], row["polarity"], row["w_source"], row["w_magnitude"]) == (
        "declared",
        1,
        0.3,
        1.0,
    )
