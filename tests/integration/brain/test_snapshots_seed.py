"""Снимки (задача 10) и стартовое наполнение анти-AI правилами (задача 9)."""

from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import knowledge, make_ctx, new_account
from xcopilot.brain import seed, snapshots
from xcopilot.brain.scoring import recompute
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo
from xcopilot.style.global_rules import read_seed

D1, D2, D3 = date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)


async def test_snapshot_once_per_day(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(3):
        await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    assert await snapshots.take(ctx, D1) == 3
    assert await snapshots.take(ctx, D1) == 0
    n = (
        await session.execute(
            text("SELECT count(*) FROM knowledge_snapshots WHERE account_id = :a"), {"a": acc}
        )
    ).scalar_one()
    assert n == 3


async def test_state_on_date_uses_latest_snapshot(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    await snapshots.take(ctx, D1)
    await KnowledgeItemsRepo(session, acc).update(iid, status="active")
    await snapshots.take(ctx, D3)
    [on_d2] = await snapshots.state_on(ctx, D2)
    [on_d3] = await snapshots.state_on(ctx, D3)
    assert (on_d2.snapshot_date, on_d2.status) == (D1, "candidate")
    assert (on_d3.snapshot_date, on_d3.status) == (D3, "active")
    assert await snapshots.state_on(ctx, date(2026, 10, 1)) == []


async def global_seed_items(session: AsyncSession) -> list[UUID]:
    glob = await AccountsRepo(session).get_global()
    rows = await session.execute(
        text(
            "SELECT id, risk_class, layer, origin, kind, target_metric, status FROM knowledge_items"
            " WHERE account_id = :a AND origin = 'seed'"
        ),
        {"a": glob.id},
    )
    out = []
    for r in rows.all():
        assert (r.risk_class, r.layer, r.origin, r.kind, r.target_metric, r.status) == (
            "C",
            "humanness",
            "seed",
            "anti_pattern",
            "quality",
            "active",
        )
        out.append(r.id)
    return out


async def test_anti_ai_seed_loaded_once_as_class_c(session: AsyncSession) -> None:
    expected = len(read_seed())
    assert expected > 0
    assert await seed.load_global_seed(session) == expected
    assert await seed.load_global_seed(session) == 0
    assert len(await global_seed_items(session)) == expected


async def test_seed_copied_to_account_with_parent_and_halved_prior(session: AsyncSession) -> None:
    await seed.load_global_seed(session)
    parents = set(await global_seed_items(session))
    acc = await new_account(session)
    ctx = make_ctx(session, acc)
    copies = await seed.copy_seed_to_account(ctx)
    assert len(copies) == len(parents)
    assert await seed.copy_seed_to_account(ctx) == []
    # половинный априор переживает пересчёт весов
    await recompute(ctx)
    rows = (
        await session.execute(
            text(
                "SELECT parent_item_id, risk_class, layer, origin,"
                " eff_alpha::float, eff_beta::float,"
                " fit_alpha::float, fit_beta::float FROM knowledge_items WHERE account_id = :a"
            ),
            {"a": acc},
        )
    ).all()
    assert {r.parent_item_id for r in rows} == parents
    for r in rows:
        assert (r.risk_class, r.layer, r.origin) == ("C", "humanness", "seed")
        assert (r.eff_alpha, r.eff_beta, r.fit_alpha, r.fit_beta) == (0.5, 0.5, 1.0, 0.5)
