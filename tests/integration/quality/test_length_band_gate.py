"""Ворота: вариант вне своей длины уходит на переписывание, а не в отказ (feat/reply-lengths)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.quality_stand import GateStand, good, make_gate_stand
from tests.writer_stand import draft_reply, post_task
from xcopilot.writer.schemas import TargetPost

SF = async_sessionmaker[AsyncSession]
TARGET = TargetPost(text="we sponsor streamers for casino traffic", url="https://x.com/o/status/7")
SHORT = "lol streamers always leave first"  # 33
MEDIUM = "we ran one of these, retention fell off a cliff the week the deal ended, ugly"  # ~78
LONG = (
    "we tried a streamer deal last year and it looked great for two months, then the streamer "
    "moved on and most of the audience went with him, so now we only count retained users"
)  # ~170
TOO_LONG_FOR_SHORT = MEDIUM + " and we never really recovered from that one"


async def three(gs: GateStand, *texts: str) -> list[UUID]:
    gs.ws.writer.push(draft_reply(*texts))
    run = await gs.ws.service.generate(
        gs.account_id, post_task(fmt="reply", lang="en", topic="casino streamers"), target=TARGET
    )
    return [d.draft_id for d in run.drafts]


async def test_variants_in_their_bands_pass_untouched(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    ids = await three(gs, SHORT, MEDIUM, LONG)
    gs.judge.push(good(), good(), good())
    for draft_id in ids:
        decision: Any = await gs.run(draft_id)
        assert decision.outcome == "pass" and decision.rewrites == 0
    assert gs.rewriter.calls == []


async def test_short_variant_too_long_is_rewritten_with_limit(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    ids = await three(gs, TOO_LONG_FOR_SHORT, MEDIUM, LONG)
    gs.judge.push(good())
    gs.rewriter.push(SHORT)

    decision: Any = await gs.run(ids[0])

    assert decision.outcome == "pass" and decision.rewrites == 1
    lines = gs.rewriter.calls[0]["instructions"]
    assert any("70" in ln and "знак" in ln for ln in lines), lines


async def test_long_variant_too_short_is_rewritten(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    ids = await three(gs, SHORT, MEDIUM, "fair, our streamer deal looked great until month two")
    gs.judge.push(good())
    gs.rewriter.push(LONG)

    decision: Any = await gs.run(ids[2])

    assert decision.outcome == "pass" and decision.rewrites == 1
    assert any("140" in ln for ln in gs.rewriter.calls[0]["instructions"])


async def test_after_limit_best_variant_shown_not_refusal(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    ids = await three(gs, TOO_LONG_FOR_SHORT, MEDIUM, LONG)
    gs.rewriter.push(TOO_LONG_FOR_SHORT + " again", TOO_LONG_FOR_SHORT)

    decision: Any = await gs.run(ids[0])

    assert decision.outcome == "needs_owner" and "max_rewrites" in decision.flags
    sql = (
        "SELECT status FROM drafts WHERE variant_no = 1 AND run_id = "
        "(SELECT run_id FROM drafts WHERE id = :d) ORDER BY revision"
    )
    rows = await gs.rows(sql, d=ids[0])
    assert rows[-1] == ("needs_owner",)


async def test_single_variant_has_no_band(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    gs.ws.writer.push(draft_reply(TOO_LONG_FOR_SHORT))
    run = await gs.ws.service.generate(
        gs.account_id, post_task(fmt="reply", lang="en", topic="casino streamers"), n=1, target=TARGET
    )
    gs.judge.push(good())
    decision: Any = await gs.run(run.drafts[0].draft_id)
    assert decision.outcome == "pass" and gs.rewriter.calls == []
