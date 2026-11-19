"""Строгие ворота для ответов в петле (ветка feat/reply-gate-strict).

Ответ, где судья «не уверен», но назвал шаблон (≥ 0.4) или слабую зацепку, уходит на круг
переписывания до показа; после лимита владелец видит лучший вариант цепочки, а не последний.
Посты — как было.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.quality_stand import GateStand, judge_answers, make_gate_stand
from tests.writer_stand import draft_reply, post_task
from xcopilot.llm.schemas import ChoiceA, ScoreA, Verdict
from xcopilot.writer.schemas import TargetPost

SF = async_sessionmaker[AsyncSession]
TARGET = TargetPost(text="we sponsor streamers for casino traffic", url="https://x.com/o/status/7")
V1 = "streamer viewers follow the streamer, so a good chunk of your retained users leave with him"
V2 = "we ran one of these, retention fell off a cliff the week the deal ended"
V3 = "fair, our streamer deal looked great until month two"
LINT_BAD = "the real trap is the streamer leaving"


def scale(score: float, conf: float) -> ScoreA:
    return ScoreA(score=score, confidence=conf, probabilities={round(score): conf})


def unsure_kicker(quote: str = "a good chunk of your retained users leave with him") -> Verdict:
    ans = judge_answers(
        voice_match=scale(2.0, 0.6),
        ai_smell=scale(2.0, 0.55),
        hook=scale(1.0, 0.55),
        ai_slop=ChoiceA(choice="kicker", confidence=0.45, probabilities={"kicker": 0.45},
                        quote=quote),
    )  # fmt: skip
    return Verdict(answers=ans, judge="deepseek-v4-pro")


def strong() -> Verdict:
    return Verdict(answers=judge_answers(hook=scale(2.0, 0.9)), judge="deepseek-v4-pro")


def only_hook() -> Verdict:
    return Verdict(answers=judge_answers(hook=scale(1.0, 0.9)), judge="deepseek-v4-pro")


async def reply_draft(gs: GateStand, text: str, fmt: str = "reply") -> UUID:
    gs.ws.writer.push(draft_reply(text))
    run = await gs.ws.service.generate(
        gs.account_id, post_task(fmt=fmt, lang="en", topic="casino streamer deals"), n=1,
        target=TARGET,
    )  # fmt: skip
    return run.drafts[0].draft_id


async def test_unsure_reply_rewritten_before_owner_sees_it(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await reply_draft(gs, V1)
    gs.judge.push(unsure_kicker(), strong())
    gs.rewriter.push(V2)

    decision: Any = await gs.run(draft_id)

    assert decision.outcome == "pass" and decision.rewrites == 1
    lines = gs.rewriter.calls[0]["instructions"]
    assert any("a good chunk of your retained users leave with him" in ln for ln in lines), lines
    assert [c.purpose for c in gs.judge.calls] == ["draft.judge.reply"] * 2


async def test_unsure_post_still_goes_to_owner(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await gs.draft()
    gs.judge.push(unsure_kicker("блобы снизили комиссии"))

    decision: Any = await gs.run(draft_id)

    assert decision.outcome == "needs_owner" and "judge_unsure" in decision.flags
    assert gs.rewriter.calls == []
    assert [c.purpose for c in gs.judge.calls] == ["draft.judge"]


async def test_quote_is_strict_too(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await reply_draft(gs, V1, fmt="quote")
    gs.judge.push(unsure_kicker(), strong())
    gs.rewriter.push(V2)

    decision: Any = await gs.run(draft_id)

    assert decision.outcome == "pass" and decision.rewrites == 1


async def test_after_limit_owner_sees_best_variant(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await reply_draft(gs, V1)
    # рев. 1 — одна причина (зацепка), рев. 2 — провал линтера, рев. 3 — три причины
    gs.judge.push(only_hook(), unsure_kicker("our streamer deal looked great"))
    gs.rewriter.push(LINT_BAD, V3)

    decision: Any = await gs.run(draft_id)

    assert decision.outcome == "needs_owner" and "max_rewrites" in decision.flags
    assert decision.rewrites == 2 and len(gs.rewriter.calls) == 2
    rows = await gs.rows("SELECT text, status FROM drafts ORDER BY revision")
    assert rows[-1] == (V1, "needs_owner")  # лучший вариант — первый, не пустота
    assert all(status == "expired" for _, status in rows[:-1])


async def test_after_limit_last_variant_kept_when_it_is_best(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await reply_draft(gs, V1)
    gs.judge.push(unsure_kicker(), unsure_kicker("retention fell off a cliff"), only_hook())
    gs.rewriter.push(V2, V3)

    decision: Any = await gs.run(draft_id)

    assert decision.outcome == "needs_owner" and "max_rewrites" in decision.flags
    rows = await gs.rows("SELECT text, status FROM drafts ORDER BY revision")
    assert len(rows) == 3 and rows[-1] == (V3, "needs_owner")
