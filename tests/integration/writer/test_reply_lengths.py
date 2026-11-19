"""Писатель: три варианта ответа разной длины (ветка feat/reply-lengths, решение 19.11.2026)."""

from __future__ import annotations

import json

import pytest

from tests.writer_stand import DISTINCT, WriterStand, draft_reply, post_task
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.writer.schemas import TargetPost

TARGET = TargetPost(text="we sponsor streamers", url="https://x.com/o/status/9", author="o")


async def test_reply_prompt_assigns_one_length_per_variant(ws: WriterStand) -> None:
    ws.writer.push(draft_reply(*DISTINCT))
    run = await ws.service.generate(ws.account_id, post_task(fmt="reply"), target=TARGET)

    prompt = ws.prompt()
    assert "70" in prompt and "140" in prompt and "220" in prompt
    assert [d.length for d in run.drafts] == ["short", "medium", "long"]
    rows = await ws.rows("SELECT context_pack FROM generation_runs")
    assert rows[0][0]["lengths"] == {"1": "short", "2": "medium", "3": "long"}


async def test_quote_gets_lengths_too(ws: WriterStand) -> None:
    ws.writer.push(draft_reply(*DISTINCT))
    run = await ws.service.generate(ws.account_id, post_task(fmt="quote"), target=TARGET)
    assert [d.length for d in run.drafts] == ["short", "medium", "long"]


@pytest.mark.parametrize("fmt", ["post", "thread"])
async def test_other_formats_have_no_lengths(ws: WriterStand, fmt: str) -> None:
    ws.writer.push(draft_reply(*DISTINCT))
    run = await ws.service.generate(ws.account_id, post_task(fmt=fmt))
    assert all(d.length is None for d in run.drafts)
    assert "Длина вариантов (строго" not in ws.prompt()


async def test_probation_variant_takes_last_length_no_fourth(ws: WriterStand) -> None:
    probe = await ws.knowledge_item("Заверши наблюдением", slot="probation")
    ws.probation.item = probe
    ws.writer.push(draft_reply(DISTINCT[0], DISTINCT[1]), draft_reply(DISTINCT[2]))

    run = await ws.service.generate(ws.account_id, post_task(fmt="reply"), target=TARGET)

    assert len(run.drafts) == 3
    assert [d.length for d in run.drafts] == ["short", "medium", "long"]
    assert run.drafts[2].probation_item_id == probe.item_id
    regular, probation = ws.prompt(0), ws.prompt(1)
    assert "long" not in regular.lower() or "220" not in regular  # обычному вызову — 2 длины
    assert "220" in probation and "70" not in probation  # пробному — только длинная


async def test_more_variants_again_three_lengths(ws: WriterStand) -> None:
    ws.writer.push(draft_reply(*DISTINCT), draft_reply(*DISTINCT))
    first = await ws.service.generate(ws.account_id, post_task(fmt="reply"), target=TARGET)
    second = await ws.service.generate(ws.account_id, post_task(fmt="reply"), target=TARGET)
    assert [d.length for d in first.drafts] == [d.length for d in second.drafts]


async def test_reused_run_keeps_lengths(ws: WriterStand) -> None:
    from uuid import uuid4

    job = uuid4()
    ws.writer.push(draft_reply(*DISTINCT))
    await ws.service.generate(ws.account_id, post_task(fmt="reply"), target=TARGET, job_id=job)
    again = await ws.service.generate(
        ws.account_id, post_task(fmt="reply"), target=TARGET, job_id=job
    )
    assert again.reused and [d.length for d in again.drafts] == ["short", "medium", "long"]


def test_prompt_template_has_lengths_slot_and_keeps_rules() -> None:
    text = PromptRegistry().get("writer.draft.v1").template
    assert "{{ lengths }}" in text
    assert "no-ai-slop" in text and "Не советуй автору" in text
    assert "Просто ответь так, как ответил бы любой рядовой пользователь Твиттера" in text
    assert json.dumps(text)  # шаблон — обычный текст
