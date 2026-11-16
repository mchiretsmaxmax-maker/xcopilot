"""Этап 13: разбор правки «до / после» (без БД и моделей)."""

from __future__ import annotations

from xcopilot.learning.diffing import classify_edit


def test_classify_edit_detects_removed_phrase() -> None:
    s = classify_edit(
        "Let's dive into how L2 fees dropped 40% this month",
        "L2 fees dropped 40% this month",
    )
    assert "removed_phrase" in s.kinds
    assert s.removed == ("let's dive into how",)
    assert s.removed_at_start is True


def test_classify_edit_detects_shortening() -> None:
    before = "Fees dropped a lot this month. Honestly it was a really big change for everyone."
    after = "Fees dropped a lot this month."
    s = classify_edit(before, after)
    assert "shortening" in s.kinds
    assert s.length_ratio < 0.6
    assert s.removed_at_start is False


def test_classify_edit_detects_casing_change() -> None:
    s = classify_edit("Fees Dropped Again This Month", "fees dropped again this month")
    assert s.casing == "lower"
    assert "casing" in s.kinds
    assert "removed_phrase" not in s.kinds


def test_classify_edit_detects_word_replacement_and_identity() -> None:
    s = classify_edit("We utilize cheap fees today", "We use cheap fees today")
    assert "word_replace" in s.kinds
    assert s.replacements == (("utilize", "use"),)
    same = classify_edit("Same text", "Same text")
    assert same.kinds == frozenset()
