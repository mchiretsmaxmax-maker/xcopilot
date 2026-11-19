"""Линтер: вариант ответа вне своей длины — ошибка «до N знаков» (feat/reply-lengths)."""

from __future__ import annotations

from xcopilot.quality.instructions import to_rewrite_instructions
from xcopilot.quality.lint.engine import LintContext, LintDraft, Linter
from xcopilot.quality.lint.rules.length_band import LengthBandRule
from xcopilot.quality.policy import Decision, Reason

CTX = LintContext()


def run(text: str, slot: str | None, fmt: str = "reply") -> list[str]:
    draft = LintDraft(text=text, format=fmt, length_slot=slot)
    return [f.message for f in LengthBandRule().check(draft, CTX)]


def test_short_over_limit_flagged_with_target() -> None:
    msgs = run("x" * 100, "short")
    assert len(msgs) == 1 and "70" in msgs[0]


def test_in_band_passes() -> None:
    assert run("x" * 65, "short") == []
    assert run("x" * 100, "medium") == []
    assert run("x" * 200, "long") == []


def test_too_short_flagged() -> None:
    msgs = run("x" * 30, "long")
    assert len(msgs) == 1 and "140" in msgs[0]


def test_no_slot_or_other_format_not_checked() -> None:
    assert run("x" * 250, None) == []
    assert run("x" * 250, "short", fmt="post") == []
    assert run("x" * 250, "short", fmt="thread") == []


def test_quote_is_checked_too() -> None:
    assert run("x" * 100, "short", fmt="quote")


def test_rule_registered_and_error_level() -> None:
    assert any(r.id == "length_band" for r in Linter().rules)
    report = Linter().run(LintDraft(text="a" * 120, format="reply", length_slot="short"), CTX)
    assert [f.rule for f in report.errors] == ["length_band"]


def test_rewrite_instruction_mentions_limit() -> None:
    report = Linter().run(LintDraft(text="a" * 120, format="reply", length_slot="short"), CTX)
    finding = report.errors[0]
    decision = Decision(
        outcome="rewrite",
        reasons=[Reason(source="lint", code="length_band", message=finding.message)],
        lint=report,
    )
    line = to_rewrite_instructions(decision)[0]
    assert "70" in line and "знак" in line
