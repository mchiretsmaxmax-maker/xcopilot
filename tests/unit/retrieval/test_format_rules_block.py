"""Писатель получает правила только своего формата (feat/style-formats)."""

from __future__ import annotations

from xcopilot.brain.schemas import TaskSpec
from xcopilot.retrieval.context_pack import ContextPack
from xcopilot.style.passport import StylePassport

RULES = {"post": "ПОСТ-ПРАВИЛО", "reply": "ОТВЕТ-ПРАВИЛО", "thread": "ТРЕД-ПРАВИЛО"}


def render(fmt: str) -> str:
    body = StylePassport(tone="спокойный", format_rules=RULES).model_dump(mode="json")
    pack = ContextPack(
        task=TaskSpec(format=fmt, lang="ru", topic="блобы и l2"),
        budget_tokens=3000,
        passport_version=7,
        passport=body,
        hard_rules=body["hard_rules"],
    )
    return pack.render()


def test_reply_pack_has_only_reply_rules() -> None:
    text = render("reply")
    assert "# Правила для ответов (обязательны)\nОТВЕТ-ПРАВИЛО" in text
    assert "ПОСТ-ПРАВИЛО" not in text and "ТРЕД-ПРАВИЛО" not in text
    assert "format_rules" not in text


def test_post_pack_has_only_post_rules() -> None:
    text = render("post")
    assert "# Правила для постов (обязательны)\nПОСТ-ПРАВИЛО" in text
    assert "ОТВЕТ-ПРАВИЛО" not in text


def test_no_rules_for_format_no_section() -> None:
    assert "Правила для цитат" not in render("quote")
    assert "ОТВЕТ-ПРАВИЛО" not in render("quote")
