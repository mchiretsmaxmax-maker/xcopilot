"""Доп. тесты ворот (не из red-набора): строка карточки, инструкции, настройки, края правил."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tests.quality_stand import judge_answers
from xcopilot.llm.schemas import ChoiceA, NoulA, Question, ScoreA, Verdict
from xcopilot.quality.instructions import to_rewrite_instructions
from xcopilot.quality.judge import QUESTIONS_GATE2, JudgeVerdict, judge
from xcopilot.quality.knowledge import phrases_of
from xcopilot.quality.lint.engine import Finding, LintContext, LintDraft, Linter, LintReport
from xcopilot.quality.lint.numbers import extract_numbers, normalize
from xcopilot.quality.policy import Decision, Reason, Thresholds, decide
from xcopilot.quality.render import gate_line
from xcopilot.quality.settings import global_banned_phrases, hard_rules_of
from xcopilot.retrieval.context_pack import ContextPack, TaskSpec
from xcopilot.style.passport import HardRules


def verdict(**over: Any) -> JudgeVerdict:
    return JudgeVerdict.from_verdict(Verdict(answers=judge_answers(**over), judge="j"))


def test_gate_line_variants() -> None:
    ok = decide(LintReport(text="x"), verdict(), Thresholds())
    assert gate_line(ok) == "Проверка: линтер ✅ · судья ✅ голос 2.6/3, AI 2.8/3"
    unsure = ScoreA(score=1.8, confidence=0.3, probabilities={2: 1.0})
    line = gate_line(decide(LintReport(text="x"), verdict(voice_match=unsure), Thresholds()))
    assert line == "⚠️ Судья не уверен: голос 1.8/3, AI 2.8/3"
    fb = decide(LintReport(text="x"), verdict(), Thresholds())
    fb.flags.append("fallback_judge")
    assert gate_line(fb).endswith("Резервный судья")
    lint_only = decide(LintReport(text="x"), None, Thresholds())
    assert gate_line(lint_only) == "Проверка: линтер ✅ · Проверен только правилами"
    risky = decide(LintReport(text="x"), verdict(risky=NoulA(p=0.9)), Thresholds())
    assert "риск" in gate_line(risky)
    capped = Decision(outcome="needs_owner", flags=["max_rewrites"], verdict=verdict(
        voice_match=ScoreA(score=1.0, confidence=0.9, probabilities={1: 1.0})))  # fmt: skip
    assert "⚠️" in gate_line(capped) and "двух" in gate_line(capped)
    assert "голос 1/3" in gate_line(capped)


def test_instructions_for_lint_and_judge_codes() -> None:
    report = LintReport(text="первая строка\nвторая", findings=[])
    d = Decision(
        outcome="rewrite",
        lint=report,
        reasons=[
            Reason(source="lint", code="emoji", fragment="🚀"),
            Reason(source="lint", code="emoji", fragment="🚀"),
            Reason(source="judge", code="weak_hook"),
            Reason(source="judge", code="off_topic"),
            Reason(source="lint", code="unknown_rule", message="что-то", fragment="кусок"),
            Reason(source="lint", code="casing"),
        ],
    )
    lines = to_rewrite_instructions(d, topic="газ в l2")
    assert lines[0] == "Убери эмодзи «🚀»."
    assert len([x for x in lines if "🚀" in x]) == 1
    assert any("«первая строка»" in x for x in lines)
    assert any("«газ в l2»" in x for x in lines)
    assert any("кусок" in x for x in lines)
    assert "Пиши только строчными буквами." in lines


def test_hard_rules_of_tolerates_shapes() -> None:
    rules = hard_rules_of({"casing": "lower", "emoji": False, "links": True, "max_len": 240,
                           "hashtags": "rare", "banned_phrases": ["x", " "]})  # fmt: skip
    assert rules == HardRules(casing="lower", emoji="none", links="free", hashtags="rare",
                              max_len=240, banned_phrases=["x"])  # fmt: skip
    listed = hard_rules_of([{"name": "emoji", "value": "none"}, "мусор"])
    assert listed.emoji == "none"
    assert hard_rules_of(None) == HardRules()
    assert hard_rules_of({"max_len": 0}) == HardRules()  # невалидное → по умолчанию
    assert hard_rules_of(HardRules(casing="lower")).casing == "lower"
    assert hard_rules_of({"max_len": True}).max_len == 280


def test_global_banned_phrases(tmp_path: Path) -> None:
    phrases = global_banned_phrases()
    assert "подписывайтесь" in phrases and "game-changer" in phrases
    assert global_banned_phrases(tmp_path / "нет.toml") == ()


def test_phrases_of_statement() -> None:
    s = "Не пиши «в современном мире», «это не просто X, это Y» и “game changer”"
    assert phrases_of(s) == ("в современном мире", "game changer")
    assert phrases_of("без кавычек") == ()
    assert phrases_of('Не пиши "давайте разберёмся" и «…»') == ("давайте разберёмся",)


def test_thresholds_load_without_file_and_fallback_flag(tmp_path: Path) -> None:
    assert Thresholds.load(tmp_path / "нет.toml") == Thresholds()
    assert Thresholds().with_overrides(None) == Thresholds()
    low = ScoreA(score=1.0, confidence=0.9, probabilities={1: 1.0})
    issue = ChoiceA(choice="vague", confidence=0.9, probabilities={"vague": 0.9})
    v = JudgeVerdict.from_verdict(
        Verdict(answers=judge_answers(ai_smell=low, main_issue=issue), judge="j", fallback=True)
    )
    d = decide(LintReport(text="x"), v, Thresholds())
    assert d.outcome == "rewrite" and d.flags == ["fallback_judge"]
    assert [r.code for r in d.reasons] == ["ai_phrases", "vague"]
    off = verdict(on_topic=NoulA(p=0.1), grounded=NoulA(p=0.2))
    assert [r.code for r in decide(LintReport(text="x"), off, Thresholds()).reasons] == [
        "off_topic",
        "unsupported_claim",
    ]


def test_unknown_main_issue_becomes_none() -> None:
    odd = ChoiceA(choice="whatever", confidence=0.5, probabilities={})
    assert verdict(main_issue=odd).main_issue == "none"


def test_numbers_edge_cases() -> None:
    with pytest.raises(ValueError):
        normalize("без цифр")
    assert normalize("$5k") == Decimal(5000)
    assert normalize("2 млрд") == Decimal(2 * 10**9)
    tokens = extract_numbers("в 2025 году было 12% и 1,200.5")
    assert [t.skip for t in tokens] == [True, False, False]
    assert tokens[1].unit == "%" and tokens[2].value == Decimal("1200.5")


class Boom:
    id = "boom"
    level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise RuntimeError("сломалось")


class BoomFix(Boom):
    def fix(self, draft: LintDraft, ctx: LintContext) -> LintDraft | None:
        raise RuntimeError("сломалось")


def test_broken_rule_becomes_internal_warning() -> None:
    linter = Linter([Boom(), BoomFix()])  # type: ignore[list-item]
    assert len(linter.rules) == 2
    report = linter.run(LintDraft(text="x"), LintContext())
    assert [f.rule for f in report.findings] == ["internal", "internal", "internal"]
    assert report.ok and len(report.warnings) == 3


def test_hashtags_fix_keeps_post_made_of_tags() -> None:
    ctx = LintContext(hard_rules=HardRules(hashtags="none"))
    report = Linter().run(LintDraft(text="#crypto #l2"), ctx)
    assert not report.ok and report.autofixed == []


def test_thread_parts_autofixed_too() -> None:
    ctx = LintContext(hard_rules=HardRules(casing="lower", hashtags="none"))
    parts = ("Раз #a", "Два", "Три")
    report = Linter().run(LintDraft(text="\n\n".join(parts), format="thread", parts=parts), ctx)
    assert report.parts == ["раз", "два", "три"]


@pytest.mark.parametrize(
    ("text", "lang", "rule"),
    [
        ("this is not a test", "en", None),
        ("комиссии упали — а пользователи остались", "ru", None),
        ("быстро, надёжно и дёшево", "ru", "warn"),
        ("gas fell. газ подешевел заметно и сильно, правда-правда очень", "en", "error"),
    ],
)
def test_misc_rule_levels(text: str, lang: str, rule: str | None) -> None:
    report = Linter().run(LintDraft(text=text), LintContext(lang=lang))
    levels = {f.level for f in report.findings if f.rule in ("ai_patterns", "language")}
    assert levels == ({rule} if rule else set())


async def test_judge_calls_port_with_gate_questions() -> None:
    calls: list[dict[str, Question]] = []

    class Port:
        async def decide(self, **kw: Any) -> Verdict:
            calls.append(kw["questions"])
            assert kw["purpose"] == "draft.judge"
            return Verdict(answers=judge_answers(), judge="j")

    pack = ContextPack(task=TaskSpec(format="post", lang="ru", topic="тема"), budget_tokens=6000)
    draft = LintDraft(text="x", parts=("a", "b"))
    v = await judge(draft, pack, port=Port(), account_id=uuid.uuid4())
    assert v.voice_match.score == 2.6 and calls[0] is QUESTIONS_GATE2
