"""Хвост тренда: разбор из material_analyses (гипотеза), новый текст не копирует оригинал."""

from __future__ import annotations

from tests.writer_stand import DISTINCT, EXTRA, WriterStand, draft_reply
from xcopilot.writer.similarity import text_similarity
from xcopilot.writer.trend_tail import PostRef

ORIGINAL = (
    "За 30 дней я потерял 12% на мостах. Один вывод: держи деньги там, где живёшь. "
    "Всё остальное — шум."
)


async def test_trend_tail_analysis_labelled_hypothesis(ws: WriterStand) -> None:
    source = await ws.foreign_post(ORIGINAL)
    ws.writer.push(draft_reply(*DISTINCT))

    run = await ws.service.generate_tail(ws.account_id, PostRef(source_id=source))

    assert run.analysis is not None and run.analysis.available
    assert run.analysis.label == "гипотеза"
    assert "гипотеза" in ws.prompt().lower()


async def test_trend_tail_rejects_near_copy(ws: WriterStand) -> None:
    source = await ws.foreign_post(ORIGINAL)
    near = ORIGINAL.replace("12%", "15%")
    ws.writer.push(draft_reply(near, DISTINCT[1], DISTINCT[2]), draft_reply(near + " Точно."))

    run = await ws.service.generate_tail(ws.account_id, PostRef(source_id=source))

    assert len(ws.writer.calls) == 2  # одна перегенерация копии
    assert run.drafts and all(text_similarity(d.text, ORIGINAL) < 0.6 for d in run.drafts)
    assert "near_copy_dropped" in run.warnings
    assert await ws.count("drafts", "run_id = :r", r=run.run_id) == len(run.drafts) == 2


async def test_trend_tail_reuses_material_analysis(ws: WriterStand) -> None:
    source = await ws.foreign_post(ORIGINAL)
    ws.writer.push(draft_reply(*DISTINCT))

    run = await ws.service.generate_tail(ws.account_id, PostRef(source_id=source), own_topic="l2")

    assert len(ws.writer.calls) == 1  # отдельного вызова для разбора нет
    assert ws.cheap.calls == []
    assert run.analysis is not None
    assert run.analysis.hook_type == "number_first"
    assert run.analysis.structure == "claim_proof_takeaway"
    assert run.analysis.hypotheses == ["короткий зачин с цифрой цепляет"]
    prompt = ws.prompt()
    assert "короткий зачин с цифрой цепляет" in prompt
    assert "number_first" in prompt
    rows = await ws.rows("SELECT kind FROM generation_runs WHERE id = :r", r=run.run_id)
    assert rows == [("trend_tail",)]


async def test_trend_tail_without_analysis_marks_unavailable(ws: WriterStand) -> None:
    source = await ws.foreign_post(ORIGINAL, analysis=False)
    ws.writer.push(draft_reply(*DISTINCT))

    run = await ws.service.generate_tail(ws.account_id, PostRef(source_id=source))

    assert run.analysis is not None and not run.analysis.available
    assert len(ws.writer.calls) == 1
    assert len(run.drafts) == 3


async def test_trend_tail_from_plain_text(ws: WriterStand) -> None:
    ws.writer.push(draft_reply(EXTRA, DISTINCT[1], DISTINCT[2]))

    run = await ws.service.generate_tail(ws.account_id, PostRef(text=ORIGINAL))

    assert run.analysis is not None and not run.analysis.available
    assert ORIGINAL in ws.prompt()
    assert "<untrusted" in ws.prompt()
