"""Функция knowledge_recompute_scores: формулы раздела 2.4 на примерах таблицы из 1.4."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.db.brain_helpers import account, author, evidence, item, source

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


async def recompute(
    session: AsyncSession, acc: UUID, item_id: UUID | None = None, now: datetime = NOW
) -> int:
    return (
        await session.execute(
            text("SELECT knowledge_recompute_scores(:a, :i, :n)"),
            {"a": acc, "i": item_id, "n": now},
        )
    ).scalar_one()


async def scores(session: AsyncSession, item_id: UUID) -> tuple[float, float, float]:
    row = (
        await session.execute(
            text("SELECT eff_conf, fit_conf, quality FROM knowledge_items WHERE id = :i"),
            {"i": item_id},
        )
    ).one()
    return round(float(row[0]), 2), round(float(row[1]), 2), round(float(row[2]), 2)


async def foreign_hits(session: AsyncSession, acc: UUID, iid: UUID, n: int) -> None:
    """n чужих хитов со скриншотов от разных авторов: w = 0.7 × 1.0 × 1.0 × 1."""
    for _ in range(n):
        auth = await author(session, acc, f"author{await _seq(session)}")
        sid = await source(session, acc, stance="inspiration", origin="screenshot", author_id=auth)
        await evidence(
            session, acc, iid, observed_at=NOW, source_id=sid, author_id=auth, w_source=0.7
        )


_counter = {"n": 0}


async def _seq(session: AsyncSession) -> int:
    _counter["n"] += 1
    return _counter["n"]


async def own_outcomes(session: AsyncSession, acc: UUID, iid: UUID, n: int, polarity: int) -> None:
    """n исходов на своих постах: w = 1.0 × 2.0 × 1.0."""
    for _ in range(n):
        sid = await source(session, acc, stance="own", origin="x_api")
        await evidence(
            session,
            acc,
            iid,
            observed_at=NOW,
            source_id=sid,
            basis="own_outcome",
            polarity=polarity,
            w_scope=2.0,
        )


async def decisions(
    session: AsyncSession, acc: UUID, iid: UUID, n: int, weight: float, polarity: int
) -> None:
    for _ in range(n):
        await evidence(
            session,
            acc,
            iid,
            observed_at=NOW,
            basis="owner_decision",
            dimension="fit",
            polarity=polarity,
            w_source=weight,
        )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("new", (0.21, 0.43, 0.30)),
        ("3_foreign_hits", (0.57, 0.43, 0.51)),
        ("3_hits_2_approved", (0.57, 0.64, 0.59)),
        ("3_hits_2_own_hits", (0.77, 0.43, 0.63)),
        ("3_hits_2_own_flops", (0.22, 0.43, 0.31)),
        ("10_foreign_hits", (0.70, 0.43, 0.59)),
        ("3_hits_2_not_my_voice", (0.57, 0.16, 0.40)),
    ],
)
async def test_recompute_matches_section_1_4_table(
    session: AsyncSession, case: str, expected: tuple[float, float, float]
) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    if case == "10_foreign_hits":
        await foreign_hits(session, acc, iid, 10)
    elif case != "new":
        await foreign_hits(session, acc, iid, 3)
    if case == "3_hits_2_approved":
        await decisions(session, acc, iid, 2, 1.0, 1)
    if case == "3_hits_2_own_hits":
        await own_outcomes(session, acc, iid, 2, 1)
    if case == "3_hits_2_own_flops":
        await own_outcomes(session, acc, iid, 2, -1)
    if case == "3_hits_2_not_my_voice":
        await decisions(session, acc, iid, 2, 1.5, -1)
    assert await recompute(session, acc, iid) == 1
    assert await scores(session, iid) == pytest.approx(expected, abs=0.011)


async def test_recompute_counters_and_scope(session: AsyncSession) -> None:
    acc, other = await account(session), await account(session)
    iid = await item(session, acc)
    untouched = await item(session, other)
    await foreign_hits(session, acc, iid, 3)
    await own_outcomes(session, acc, iid, 2, 1)
    await own_outcomes(session, acc, iid, 1, -1)
    assert await recompute(session, acc) == 1  # только свой аккаунт
    row = (
        await session.execute(
            text(
                "SELECT evidence_n, independent_authors, own_support, own_contra, occurrences,"
                " last_evidence_at, scores_updated_at FROM knowledge_items WHERE id = :i"
            ),
            {"i": iid},
        )
    ).one()
    assert tuple(row[:5]) == (6, 3, 2, 1, 6)
    assert row[5] == NOW and row[6] == NOW
    assert await scores(session, untouched) == (0.21, 0.43, 0.30)


async def test_recompute_decay_revocation_and_past_date(session: AsyncSession) -> None:
    acc = await account(session)
    craft = await item(session, acc)
    platform = await item(session, acc, layer="platform")
    sid = await source(session, acc)
    old = NOW - timedelta(days=180)
    # одно доказательство весом 2.0, возраст ровно период полураспада → вес 1.0
    await evidence(session, acc, craft, observed_at=old, source_id=sid, w_scope=2.0)
    await evidence(session, acc, platform, observed_at=old, source_id=sid, w_scope=2.0)
    await recompute(session, acc, now=NOW)
    alpha = {
        r[0]: float(r[1])
        for r in (
            await session.execute(
                text("SELECT id, eff_alpha FROM knowledge_items WHERE account_id = :a"), {"a": acc}
            )
        ).all()
    }
    assert alpha[craft] == pytest.approx(2.0, abs=1e-3)  # 1 + 2.0 × 0.5
    assert alpha[platform] == pytest.approx(1.5, abs=1e-3)  # H = 90: 1 + 2.0 × 0.25
    # отозванное доказательство не учитывается после отзыва, но учитывается на дату до отзыва
    await session.execute(
        text(
            "UPDATE knowledge_evidence SET revoked_at = :t, revoked_reason = 'source_removed'"
            " WHERE item_id = :i"
        ),
        {"t": NOW - timedelta(days=1), "i": craft},
    )
    await recompute(session, acc, craft, now=NOW)
    assert await scores(session, craft) == (0.21, 0.43, 0.30)
    await recompute(session, acc, craft, now=NOW - timedelta(days=2))
    assert (await scores(session, craft))[0] > 0.21
    # доказательство, записанное позже p_now, не учитывается
    await recompute(session, acc, platform, now=old - timedelta(days=1))
    assert await scores(session, platform) == (0.21, 0.43, 0.30)


async def test_quality_target_uses_fit_only(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc, target_metric="quality")
    await decisions(session, acc, iid, 2, 1.0, 1)
    await recompute(session, acc, iid)
    _eff, fit, quality = await scores(session, iid)
    assert quality == fit == 0.64
