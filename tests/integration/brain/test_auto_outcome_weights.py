"""Автоисход доходит до весов знаний; смена исхода пересматривает доказательства (ADR 0033)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import knowledge, make_ctx, material, new_account, new_author
from xcopilot.brain.evidence import record_from_material, revise_material
from xcopilot.db.repos.materials import MaterialOutcomesRepo


async def live(session: AsyncSession, item_id: UUID) -> list[dict[str, Any]]:
    res = await session.execute(
        text(
            "SELECT basis, polarity, outcome_tier, w_source::float AS w_source"
            " FROM knowledge_evidence WHERE item_id = :i AND revoked_at IS NULL ORDER BY id"
        ),
        {"i": item_id},
    )
    return [dict(r._mapping) for r in res.all()]


async def test_auto_outcome_weight_below_owner_label(session: AsyncSession) -> None:
    acc = await new_account(session, niche="crypto")
    item = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    auto = await material(
        session,
        acc,
        author_id=await new_author(session, acc, niche="crypto"),
        tier="hit",
        signal_origin="public_metrics",
        outcome_basis="rates",
    )
    manual = await material(
        session,
        acc,
        author_id=await new_author(session, acc, niche="crypto"),
        tier="hit",
        signal_origin="owner_label",
        outcome_basis="owner_label",
    )
    assert await record_from_material(ctx, auto) == 1
    assert await record_from_material(ctx, manual) == 1
    rows = await live(session, item)
    assert [(r["polarity"], r["w_source"]) for r in rows] == [(1, 0.3), (1, 0.4)]
    assert ctx.config.weights.source["public_metrics"] < ctx.config.weights.source["owner_label"]


async def test_revise_material_follows_new_outcome(session: AsyncSession) -> None:
    acc = await new_account(session, niche="crypto")
    item = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    sid = await material(
        session,
        acc,
        author_id=await new_author(session, acc, niche="crypto"),
        tier="hit",
        signal_origin="public_metrics",
        outcome_basis="rates",
    )
    assert await record_from_material(ctx, sid) == 1
    assert [r["polarity"] for r in await live(session, item)] == [1]
    # владелец поправил: «не зашёл»
    await MaterialOutcomesRepo(session, acc).add_current(
        sid,
        method_version=1,
        basis="owner_label",
        signal_origin="owner_label",
        reliability=0.4,
        **{f"tier_{g}": "flop" for g in ("reach", "engagement", "replies", "saves")},
    )
    assert await revise_material(ctx, sid) == 1
    rows = await live(session, item)
    assert [(r["polarity"], r["outcome_tier"], r["w_source"]) for r in rows] == [(-1, "flop", 0.4)]
    revoked = await session.execute(
        text(
            "SELECT count(*) FROM knowledge_evidence WHERE item_id = :i AND revoked_at IS NOT NULL"
        ),
        {"i": item},
    )
    assert revoked.scalar() == 1
