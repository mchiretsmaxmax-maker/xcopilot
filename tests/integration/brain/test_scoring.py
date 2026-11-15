"""Веса (этап 9A, задача 8): таблица из раздела 1.4, спад по слою, потолок, дата, отзыв."""

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_stand import NOW, knowledge, make_ctx, new_account
from tests.integration.db.brain_helpers import author, evidence, source
from xcopilot.brain.evidence import revoke
from xcopilot.brain.scoring import recompute


async def scores(session: AsyncSession, iid: UUID) -> tuple[float, float, float]:
    row = (
        await session.execute(
            text("SELECT eff_conf, fit_conf, quality FROM knowledge_items WHERE id = :i"),
            {"i": iid},
        )
    ).one()
    return round(float(row[0]), 2), round(float(row[1]), 2), round(float(row[2]), 2)


async def alpha(session: AsyncSession, iid: UUID) -> float:
    return float(
        (
            await session.execute(
                text("SELECT eff_alpha FROM knowledge_items WHERE id = :i"), {"i": iid}
            )
        ).scalar_one()
    )


async def foreign_hits(session: AsyncSession, acc: UUID, iid: UUID, n: int) -> list[UUID]:
    out = []
    for i in range(n):
        auth = await author(session, acc, f"h{i}_{iid.hex[:6]}")
        sid = await source(session, acc, origin="screenshot", author_id=auth)
        await evidence(
            session, acc, iid, observed_at=NOW, source_id=sid, author_id=auth, w_source=0.7
        )
        out.append(sid)
    return out


async def own(session: AsyncSession, acc: UUID, iid: UUID, n: int, polarity: int) -> None:
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


async def reviews(session: AsyncSession, acc: UUID, iid: UUID, n: int, w: float) -> None:
    from tests.integration.db.brain_helpers import draft

    for _ in range(n):
        d = await draft(session, acc)
        await evidence(
            session,
            acc,
            iid,
            observed_at=NOW,
            draft_id=d,
            dimension="fit",
            basis="review",
            polarity=1 if w > 0 else -1,
            w_source=abs(w),
        )


# (подпись строки, чужих хитов, своих хитов, своих провалов, одобрений, отказов «не мой голос»)
REFERENCE = [
    ("new", 0, 0, 0, 0, 0, (0.21, 0.43, 0.30)),
    ("3 foreign hits", 3, 0, 0, 0, 0, (0.57, 0.43, 0.51)),
    ("+2 approvals", 3, 0, 0, 2, 0, (0.57, 0.64, 0.59)),
    ("+2 own hits", 3, 2, 0, 0, 0, (0.77, 0.43, 0.63)),
    ("+2 own flops", 3, 0, 2, 0, 0, (0.22, 0.43, 0.31)),
    ("10 foreign hits", 10, 0, 0, 0, 0, (0.70, 0.43, 0.59)),
    ("+2 voice rejects", 3, 0, 0, 0, 2, (0.57, 0.16, 0.40)),
]


@pytest.mark.parametrize(("label", "fh", "oh", "of", "ap", "vr", "expected"), REFERENCE)
async def test_scores_match_reference_table(
    session: AsyncSession,
    label: str,
    fh: int,
    oh: int,
    of: int,
    ap: int,
    vr: int,
    expected: tuple[float, float, float],
) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    await foreign_hits(session, acc, iid, fh)
    await own(session, acc, iid, oh, 1)
    await own(session, acc, iid, of, -1)
    await reviews(session, acc, iid, ap, 1.0)
    await reviews(session, acc, iid, vr, -1.5)
    assert await recompute(make_ctx(session, acc), now=NOW) == 1
    assert await scores(session, iid) == expected, label


async def test_decay_half_life_by_layer(session: AsyncSession) -> None:
    acc = await new_account(session)
    ctx = make_ctx(session, acc)
    craft = await knowledge(session, acc)
    platform = await knowledge(session, acc, layer="platform", element="packaging")
    voice = await knowledge(
        session, acc, layer="voice", element="tone", target_metric="quality", risk_class="A"
    )
    for iid, days, stance, basis in (
        (craft, 180, "inspiration", "observed_outcome"),
        (platform, 90, "inspiration", "observed_outcome"),
        (voice, 365, "own", "own_outcome"),
    ):
        sid = await source(session, acc, stance=stance)
        await evidence(
            session, acc, iid, observed_at=NOW - timedelta(days=days), source_id=sid, basis=basis
        )
    await recompute(ctx, now=NOW)
    assert [round(await alpha(session, i), 3) for i in (craft, platform, voice)] == [1.5, 1.5, 1.5]


async def test_foreign_only_capped_at_070(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    await foreign_hits(session, acc, iid, 10)
    ctx = make_ctx(session, acc)
    await recompute(ctx, iid, now=NOW)
    assert (await scores(session, iid))[0] == 0.70
    await own(session, acc, iid, 1, 1)
    await recompute(ctx, iid, now=NOW)
    assert (await scores(session, iid))[0] > 0.70


async def test_recompute_as_of_past_date(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    for days_ago in (10, 1):
        sid = await source(session, acc)
        await evidence(session, acc, iid, observed_at=NOW - timedelta(days=days_ago), source_id=sid)
    ctx = make_ctx(session, acc)
    await recompute(ctx, iid, now=NOW - timedelta(days=5))
    past = await alpha(session, iid)
    await recompute(ctx, iid, now=NOW)
    assert 1.9 < past < 2.0  # одно доказательство пятидневной давности
    assert await alpha(session, iid) > 2.9


async def test_revoked_evidence_ignored(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    [sid] = await foreign_hits(session, acc, iid, 1)
    ctx = make_ctx(session, acc)
    await recompute(ctx, iid, now=NOW)
    assert await alpha(session, iid) > 1.6
    assert await revoke(ctx, source_id=sid, reason="owner") == 1
    await recompute(ctx, iid, now=NOW)
    assert await alpha(session, iid) == 1.0
