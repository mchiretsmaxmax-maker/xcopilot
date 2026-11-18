"""Автоисход по публичным цифрам (feat/auto-outcome, ADR 0033)."""

from __future__ import annotations

from uuid import UUID

import pytest

from tests.brain_stand import BrainStand
from xcopilot.brain import outcomes, signals
from xcopilot.brain.signals import Signal

TEXT = "i lost $40k in 3 weeks\nthen i stopped\nnow i sleep fine"


def pub(**kw: int | None) -> Signal:
    return Signal(origin="public_metrics", post_age_hours=72, **kw)  # type: ignore[arg-type]


async def _outcome(brain: BrainStand, sid: UUID) -> outcomes.Outcome:
    async with brain.uow() as uow:
        return await outcomes.compute_outcome(
            uow.session, brain.account_id, sid, now=brain.clock.now()
        )


async def _with(brain: BrainStand, sig: Signal) -> UUID:
    sid = await brain.material(TEXT)
    async with brain.uow() as uow:
        await signals.add_signal(uow.session, brain.account_id, sid, sig)
    return sid


def test_auto_thresholds_in_config() -> None:
    cfg = outcomes.OutcomeConfig.load()
    lo, hi = cfg.auto_by_views
    assert 0 < lo < hi < 1
    lo_f, hi_f = cfg.auto_by_followers
    assert 0 < lo_f < hi_f < 1
    assert cfg.reliability["public_metrics"] < cfg.reliability["owner_label"]


@pytest.mark.parametrize(
    ("sig", "tier"),
    [
        # (лайки + 2·ответы + 3·ретвиты) / просмотры
        (pub(views=10000, likes=400, replies=20, reposts=10), "hit"),  # 4.7%
        (pub(views=10000, likes=100, replies=5, reposts=2), "normal"),  # 1.16%
        (pub(views=10000, likes=20, replies=0, reposts=0), "flop"),  # 0.2%
        # просмотров нет — / подписчики
        (pub(likes=300, author_followers=10000), "hit"),  # 3%
        (pub(likes=50, author_followers=10000), "normal"),  # 0.5%
        (pub(likes=5, author_followers=10000), "flop"),  # 0.05%
    ],
)
async def test_auto_outcome_by_thresholds(brain: BrainStand, sig: Signal, tier: str) -> None:
    sid = await _with(brain, sig)
    out = await _outcome(brain, sid)
    assert out.signal_origin == "public_metrics"
    assert out.basis == "rates"
    assert out.reliability == outcomes.OutcomeConfig.load().reliability["public_metrics"]
    assert (out.tiers.engagement, out.tiers.reach, out.tiers.replies, out.tiers.saves) == (
        tier,
        tier,
        tier,
        tier,
    )


async def test_no_numbers_no_outcome(brain: BrainStand) -> None:
    sid = await _with(brain, pub(likes=40))  # ни просмотров, ни подписчиков
    out = await _outcome(brain, sid)
    assert out.basis == "none"
    assert out.tiers.engagement == "unknown"


async def test_young_post_gets_no_auto_outcome_and_no_defer(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    async with brain.uow() as uow:
        await signals.add_signal(
            uow.session,
            brain.account_id,
            sid,
            Signal(origin="public_metrics", views=500, likes=40, post_age_hours=3),
        )
    out = await _outcome(brain, sid)
    assert out.basis == "none" and not out.deferred
    assert await brain.count("jobs", "kind = 'brain.outcome'") == 0


async def test_owner_label_overrides_auto(brain: BrainStand) -> None:
    sid = await _with(brain, pub(views=10000, likes=400, replies=20, reposts=10))
    assert (await _outcome(brain, sid)).tiers.engagement == "hit"
    async with brain.uow() as uow:
        await signals.label(uow.session, brain.account_id, sid, "flop", brain.user_id)
    out = await _outcome(brain, sid)
    assert (out.basis, out.signal_origin, out.tiers.engagement) == (
        "owner_label",
        "owner_label",
        "flop",
    )
    # новый автоисход позже ручного не перебивает владельца
    async with brain.uow() as uow:
        await signals.add_signal(
            uow.session, brain.account_id, sid, pub(views=20000, likes=900, replies=40)
        )
    assert (await _outcome(brain, sid)).basis == "owner_label"


async def test_changed_outcome_requeues_detect_to_revise_weights(brain: BrainStand) -> None:
    sid = await _with(brain, pub(views=10000, likes=400, replies=20, reposts=10))
    await _outcome(brain, sid)
    assert await brain.count("jobs", "kind = 'brain.detect'") == 0  # первый исход — разбор сам
    async with brain.uow() as uow:
        await signals.label(uow.session, brain.account_id, sid, "flop", brain.user_id)
    await _outcome(brain, sid)
    jobs = await brain.rows("SELECT payload FROM jobs WHERE kind = 'brain.detect'")
    assert [j.payload for j in jobs] == [{"source_id": str(sid), "revise": True}]
    await _outcome(brain, sid)  # тот же исход — повторной задачи нет
    assert await brain.count("jobs", "kind = 'brain.detect'") == 1


async def test_own_post_gets_auto_outcome(brain: BrainStand) -> None:
    sid = await brain.material(TEXT, stance="own", author_handle=None)
    async with brain.uow() as uow:
        await signals.add_signal(
            uow.session, brain.account_id, sid, pub(views=8000, likes=300, replies=10)
        )
    out = await _outcome(brain, sid)
    assert (out.basis, out.signal_origin, out.tiers.engagement) == (
        "rates",
        "public_metrics",
        "hit",
    )
