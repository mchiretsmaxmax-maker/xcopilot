"""Сигналы и исходы (этап 7A, задачи 5 и 6)."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from tests.brain_stand import BrainStand
from xcopilot.brain import outcomes, signals
from xcopilot.brain.signals import Signal

TEXT = "i lost $40k in 3 weeks\nthen i stopped\nnow i sleep fine"


async def _outcome(brain: BrainStand, sid: UUID) -> outcomes.Outcome:
    async with brain.uow() as uow:
        return await outcomes.compute_outcome(
            uow.session, brain.account_id, sid, now=brain.clock.now()
        )


async def _add(brain: BrainStand, sid: UUID, sig: Signal) -> UUID:
    async with brain.uow() as uow:
        return await signals.add_signal(uow.session, brain.account_id, sid, sig)


async def _own_posts(brain: BrainStand, views: list[int], *, kind: str = "post") -> list[UUID]:
    """Свои посты формата `kind` старше суток с метриками own_sync."""
    ids = []
    for i, v in enumerate(views):
        sid = await brain.material(
            f"own {kind} {i}",
            kind=kind,
            stance="own",
            origin="history_backfill",
            posted_at=brain.clock.now() - timedelta(days=3 + i % 60),
            analysis_status="analyzed",
        )
        await _add(
            brain,
            sid,
            Signal(
                origin="own_sync",
                views=v,
                likes=v // 50,
                replies=v // 500,
                reposts=v // 200,
                bookmarks=v // 400,
                post_age_hours=72,
            ),
        )
        ids.append(sid)
    return ids


async def test_owner_label_creates_signal(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    async with brain.uow() as uow:
        signal_id = await signals.label(uow.session, brain.account_id, sid, "hit", brain.user_id)
    row = (
        await brain.rows(
            "SELECT origin, owner_label, confirmed_by, read_confidence FROM material_signals"
            " WHERE id = :i",
            i=signal_id,
        )
    )[0]
    assert (row.origin, row.owner_label, row.confirmed_by) == ("owner_label", "hit", brain.user_id)
    assert float(row.read_confidence) == 1.0
    events = await brain.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = 'material.signal_added'",
        a=brain.account_id,
    )
    assert events[0].props["origin"] == "owner_label"
    out = await _outcome(brain, sid)
    assert out.basis == "owner_label"
    assert out.tiers.engagement == "hit"
    assert out.reliability == 0.4


async def test_default_pick_signal_when_no_label(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    async with brain.uow() as uow:
        first = await signals.default_pick(uow.session, brain.account_id, sid)
    async with brain.uow() as uow:
        again = await signals.default_pick(uow.session, brain.account_id, sid)
    assert first is not None and again is None  # один сигнал отбора на материал
    rows = await brain.rows("SELECT origin FROM material_signals WHERE source_id = :s", s=sid)
    assert [r.origin for r in rows] == ["owner_pick"]

    labelled = await brain.material(TEXT + " 2")
    async with brain.uow() as uow:
        await signals.label(uow.session, brain.account_id, labelled, "flop", brain.user_id)
        assert await signals.default_pick(uow.session, brain.account_id, labelled) is None

    own = await brain.material(TEXT + " 3", stance="own")
    async with brain.uow() as uow:
        assert await signals.default_pick(uow.session, brain.account_id, own) is None

    out = await _outcome(brain, sid)
    assert (out.basis, out.signal_origin, out.reliability) == ("owner_pick", "owner_pick", 0.25)
    assert out.tiers == outcomes.Tiers()


async def test_outcome_own_uses_percentile_within_format(brain: BrainStand) -> None:
    await _own_posts(brain, [1000 + 100 * i for i in range(30)])  # посты: 1000..3900
    await _own_posts(brain, [100_000] * 25, kind="thread")  # треды не мешают постам
    top = await brain.material(
        "own best",
        stance="own",
        origin="history_backfill",
        posted_at=brain.clock.now() - timedelta(days=2),
        analysis_status="analyzed",
    )
    await _add(
        brain,
        top,
        Signal(origin="own_sync", views=5000, likes=100, replies=10, reposts=25, post_age_hours=48),
    )
    out = await _outcome(brain, top)
    assert out.basis == "own_baseline"
    assert out.reliability == 1.0
    assert out.tiers.reach == "hit"
    assert out.percentiles["reach"] >= 0.9
    row = (
        await brain.rows(
            "SELECT basis, pct_reach, tier_reach FROM material_outcomes"
            " WHERE source_id = :s AND is_current",
            s=top,
        )
    )[0]
    assert row.basis == "own_baseline" and row.tier_reach == "hit"
    assert 0.9 <= float(row.pct_reach) <= 1.0

    async with brain.uow() as uow:
        base = await outcomes.own_percentiles(
            uow.session, brain.account_id, "post", now=brain.clock.now()
        )
    assert len(base["reach"]) == 31


async def test_outcome_own_needs_20_posts_else_rates(brain: BrainStand) -> None:
    await _own_posts(brain, [1000] * 10)
    sid = await brain.material(
        "own new",
        stance="own",
        origin="history_backfill",
        posted_at=brain.clock.now() - timedelta(days=2),
        analysis_status="analyzed",
    )
    await _add(
        brain,
        sid,
        Signal(origin="own_sync", views=10_000, likes=500, reposts=0, replies=1, post_age_hours=48),
    )
    out = await _outcome(brain, sid)
    assert out.basis == "rates"
    assert out.tiers.engagement == "hit"  # 5% > 4%
    assert out.percentiles == {}


async def test_outcome_foreign_uses_rates_and_thresholds(brain: BrainStand) -> None:
    sid = await brain.material(TEXT, posted_at=brain.clock.now() - timedelta(days=5))
    await _add(
        brain,
        sid,
        Signal(
            origin="screenshot",
            views=100_000,
            likes=1_500,
            reposts=400,
            quotes=100,  # вовлечённость 2% → above
            replies=30,  # 0.03% → below
            bookmarks=600,  # 0.6% → hit
            author_followers=40_000,  # охват 2.5 → hit
            read_confidence=0.9,
        ),
    )
    out = await _outcome(brain, sid)
    assert out.basis == "rates"
    assert out.signal_origin == "screenshot"
    assert out.reliability == 0.7
    assert (out.tiers.engagement, out.tiers.replies, out.tiers.saves, out.tiers.reach) == (
        "above",
        "below",
        "hit",
        "hit",
    )
    assert abs((out.rates.engagement or 0) - 0.02) < 1e-9


async def test_outcome_unknown_without_views_or_label(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    out = await _outcome(brain, sid)
    assert (out.basis, out.signal_origin) == ("none", "none")
    assert out.tiers == outcomes.Tiers()

    likes_only = await brain.material(
        TEXT + " likes", posted_at=brain.clock.now() - timedelta(days=3)
    )
    await _add(brain, likes_only, Signal(origin="manual_numbers", likes=340))
    out2 = await _outcome(brain, likes_only)
    assert out2.basis == "none"
    assert out2.tiers == outcomes.Tiers()
    rows = await brain.rows(
        "SELECT basis, tier_reach FROM material_outcomes WHERE source_id = :s AND is_current",
        s=likes_only,
    )
    assert [(r.basis, r.tier_reach) for r in rows] == [("none", "unknown")]


async def test_outcome_deferred_for_post_younger_than_24h(brain: BrainStand) -> None:
    posted = brain.clock.now() - timedelta(hours=5)
    sid = await brain.material(TEXT, posted_at=posted)
    await _add(
        brain, sid, Signal(origin="x_api", views=5000, likes=100, replies=3, post_age_hours=5)
    )
    out = await _outcome(brain, sid)
    assert out.deferred is True
    assert out.retry_at == posted + timedelta(hours=24)
    assert await brain.count("material_outcomes", "source_id = :s", s=sid) == 0
    jobs = await brain.rows(
        "SELECT kind, payload, run_at FROM jobs WHERE account_id = :a AND kind = 'brain.outcome'",
        a=brain.account_id,
    )
    assert [(j.payload["source_id"], j.run_at) for j in jobs] == [
        (str(sid), posted + timedelta(hours=24))
    ]
    await _outcome(brain, sid)  # повторный расчёт не плодит задач
    assert (
        await brain.count("jobs", "kind = 'brain.outcome' AND account_id = :a", a=brain.account_id)
        == 1
    )

    brain.clock.advance(20 * 3600)  # через сутки после публикации исход считается
    await _add(
        brain, sid, Signal(origin="x_api", views=9000, likes=200, replies=5, post_age_hours=25)
    )
    later = await _outcome(brain, sid)
    assert later.deferred is False and later.basis == "rates"


async def test_better_signal_makes_new_current_outcome(brain: BrainStand) -> None:
    sid = await brain.material(TEXT, posted_at=brain.clock.now() - timedelta(days=4))
    async with brain.uow() as uow:
        await signals.label(uow.session, brain.account_id, sid, "normal", brain.user_id)
    first = await _outcome(brain, sid)
    assert first.basis == "owner_label"
    same = await _outcome(brain, sid)  # тот же лучший сигнал — новой строки нет
    assert same.outcome_id == first.outcome_id

    await _add(
        brain,
        sid,
        Signal(origin="screenshot", views=50_000, likes=2_500, replies=250, read_confidence=0.8),
    )
    second = await _outcome(brain, sid)
    assert second.basis == "rates" and second.signal_origin == "screenshot"
    rows = await brain.rows(
        "SELECT basis, is_current FROM material_outcomes WHERE source_id = :s"
        " ORDER BY computed_at, id",
        s=sid,
    )
    assert [(r.basis, r.is_current) for r in rows] == [("owner_label", False), ("rates", True)]
