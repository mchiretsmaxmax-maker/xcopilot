"""AI-шаблоны в петле ворот (ветка feat/anti-ai-slop): переписывание с цитатой и лимит кругов."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.quality_stand import CLEAN, good, make_gate_stand

SF = async_sessionmaker[AsyncSession]
SLOP = "настоящая проблема в комиссиях роллапов, я вижу это по своим транзакциям"
SLOP2 = "настоящая ловушка в мостах, ликвидность уходит за сутки"
SLOP3 = "настоящая история в газе, кошелёк на l2 стоит копейки"


async def test_slop_goes_to_rewrite_with_quote(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await gs.draft(SLOP)
    gs.rewriter.push(CLEAN)
    gs.judge.push(good())

    decision = await gs.run(draft_id)

    assert decision.outcome == "pass" and decision.rewrites == 1
    lines = gs.rewriter.calls[0]["instructions"]
    assert any("настоящая проблема" in ln and "AI-шаблон" in ln for ln in lines), lines
    assert len(gs.judge.calls) == 1  # судья видел только чистый текст


async def test_slop_loop_capped_and_last_variant_kept(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)
    draft_id = await gs.draft(SLOP)
    gs.rewriter.push(SLOP2, SLOP3)

    decision = await gs.run(draft_id)

    assert decision.outcome == "needs_owner" and "max_rewrites" in decision.flags
    assert decision.rewrites == 2 and len(gs.rewriter.calls) == 2
    assert gs.judge.calls == []
    rows = await gs.rows("SELECT text, status FROM drafts ORDER BY revision")
    assert rows[-1][0] == SLOP3 and rows[-1][1] == "needs_owner"  # владелец видит вариант
