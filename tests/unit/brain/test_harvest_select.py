"""R3 (ADR 0034, п. 4): отбор топа и контраста, исход по месту среди соседей."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from xcopilot.brain.harvest import HarvestConfig, HarvestParent, select, sibling_tier
from xcopilot.x.models import XMetrics, XPost

CFG = HarvestConfig.load()
T0 = datetime(2026, 11, 18, 12, tzinfo=UTC)
PARENT = HarvestParent(post_id="1", likes=1000, posted_at=T0)


def reply(i: int, likes: int, *, minutes: int = 30, text: str | None = None) -> XPost:
    return XPost(
        id=str(100 + i),
        text=text or f"distinct thoughtful reply number {i} about fees and rollups",
        author_handle=f"user{i}",
        created_at=T0 + timedelta(minutes=minutes),
        source="fxtwitter",
        metrics=XMetrics(likes=likes),
        author_followers=1000,
        lang="en",
    )


def test_select_top_by_likes_not_api_order() -> None:
    replies = [reply(i, likes=i) for i in range(100)]
    copy = "same copypasta text everyone keeps posting under viral posts"
    replies += [reply(200, 500, text=copy), reply(201, 400, text=copy.upper() + "!")]
    random.Random(7).shuffle(replies)
    sel = select(replies, PARENT, CFG)
    assert len(sel.top) == 20
    likes = [r.metrics.likes for r in sel.top if r.metrics]
    assert likes == sorted(likes, reverse=True)
    assert likes[0] == 500 and 400 not in likes  # копипаста — один раз
    assert likes[1:] == list(range(99, 80, -1))


def test_contrast_only_from_same_window() -> None:
    top = [reply(i, likes=200) for i in range(20)]
    weak_early = [reply(50 + i, likes=5, minutes=60) for i in range(8)]
    weak_late = [reply(80 + i, likes=1, minutes=300) for i in range(5)]
    mid = [reply(90, likes=150, minutes=60)]  # > 10% медианы топа — не контраст
    sel = select([*top, *weak_early, *weak_late, *mid], PARENT, CFG)
    ids = {r.id for r in sel.contrast}
    assert len(sel.contrast) == 5
    assert ids <= {r.id for r in weak_early}
    assert not ids & {r.id for r in weak_late} and "190" not in ids


def test_sibling_tier() -> None:
    assert sibling_tier(60, 1000, 10, config=CFG.sibling) == "hit"  # share 0.06
    assert sibling_tier(25, 10_000, 3, config=CFG.sibling) == "hit"  # топ-3 и ≥ 20 лайков
    assert sibling_tier(20, 1000, 8, config=CFG.sibling) == "above"  # share 0.02
    assert sibling_tier(5, 1000, 12, config=CFG.sibling) == "normal"
    assert sibling_tier(1, 1000, 30, contrast=True, config=CFG.sibling) == "flop"
