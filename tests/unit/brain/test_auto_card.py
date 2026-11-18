"""Карточка с автоисходом и короткие числа (feat/auto-outcome, ADR 0033)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.ingest_stand import buttons
from xcopilot.bot import texts
from xcopilot.brain.card import CardView, render_card


@pytest.mark.parametrize(
    ("n", "out"),
    [(0, "0"), (999, "999"), (1200, "1.2k"), (15000, "15k"), (324921, "325k"), (2_400_000, "2.4M")],
)
def test_compact_count(n: int, out: str) -> None:
    assert texts.compact_count(n) == out


def test_auto_outcome_line() -> None:
    assert texts.material_auto_outcome("hit", 1200) == "Зашёл: 🔥 (1.2k лайков, по цифрам)"
    assert texts.material_auto_outcome("normal", 40) == "Зашёл: 👍 (40 лайков, по цифрам)"
    assert texts.material_auto_outcome("flop", None) == "Зашёл: 👎 (по цифрам)"


def test_card_with_auto_outcome_has_fix_button() -> None:
    sid = uuid4()
    text, kb = render_card(
        CardView(
            source_id=sid,
            format="post",
            author_handle="a",
            hook_title=None,
            hook_text="",
            structure_title=None,
            line_count=2,
            outcome_known=True,
            auto_outcome="Зашёл: 🔥 (1.2k лайков, по цифрам)",
        )
    )
    assert "Зашёл: 🔥 (1.2k лайков, по цифрам)" in text
    assert "Как он зашёл?" not in text
    got = buttons(kb)
    assert ("✏️ Поправить оценку", f"moe:{sid}") in got
    assert ("🗑 Убрать", f"irm:{sid}") in got
