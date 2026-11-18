"""Анти-AI-slop (ветка feat/anti-ai-slop): детектор шаблонов, правило линтера, судья, промпты.

Правила — по скилу no-ai-slop (MIT, Peter Yang, https://github.com/petergyang/no-ai-slop),
адаптированы под короткие тексты X. Списки и регэкспы — в `config/ai_slop.toml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.quality_stand import judge_answers
from xcopilot.llm.fallback_judge import parse_answers
from xcopilot.llm.prompts.registry import PACKAGED_DIR
from xcopilot.llm.schemas import ChoiceA, ChoiceQ, Verdict
from xcopilot.quality.instructions import to_rewrite_instructions
from xcopilot.quality.judge import QUESTIONS_GATE2, SLOP_OPTIONS, JudgeVerdict
from xcopilot.quality.lint.engine import LintContext, LintDraft, Linter, LintReport
from xcopilot.quality.lint.slop import SlopConfig, detect_slop, load_slop_config
from xcopilot.quality.policy import Thresholds, decide

# ── детектор ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("that range isn't retirement money. it's walk-away money.", "binary_contrast"),
        ("a handful of cycles isn't a pattern, it's a coincidence", "binary_contrast"),
        ("the machine isn't the edge then. taste is.", "binary_contrast"),
        ("the real trap in that spot is forcing it", "the_real_x"),
        ("the real cooking is quiet", "the_real_x"),
        ("The D row is the real value here.", "the_real_x"),
        ("the prompt is the easy part. the hard part is the detector", "part_setup"),
        ("the weird part is that it feels big", "part_setup"),
        ("Fallback is boring until the day it saves the whole pipeline.", "kicker"),
        ("boring compounding wins here", "kicker"),
        ("boring part but that's where the edge is rn", "kicker"),
        ("that's exactly why they get built now", "kicker"),
        ("Memory in S is right, but nobody mentions the flip side.", "faux_insight"),
        ("here's the thing about rollups", "faux_insight"),
        ("what most people miss is the funding rate", "faux_insight"),
        ("the pattern i keep seeing: the best agent projects are tiny", "colon_reveal"),
        ("gas is cheap — nobody cares", "em_dash"),
        ("we should delve into the docs", "banned_word"),
        ("this is a robust setup", "banned_word"),
        ("honestly this changes everything", "banned_word"),
    ],
)
def test_detects_pattern(text: str, code: str) -> None:
    hits = detect_slop(text, "en")
    assert code in {h.code for h in hits}, hits
    hit = next(h for h in hits if h.code == code)
    assert hit.fragment and hit.fragment.lower() in text.lower()
    assert hit.message


@pytest.mark.parametrize(
    "text",
    [
        "agents paying for api calls on their own. x402 stuff looks like memes for now",
        "leverage gets wiped both ways in a sideways december. sitting out is a fair position",
        "the failures tell you more about where the harness needs work",
        "the token unlock is next week, expect some selling",
        "that mechanic matters more than the waitlist",
        "earlier bottoms make sense tbh, everyone front-runs the cycle now",
        "real money is on the line here, so i'd size small",
        "missing the actual bit is the usual failure",
        "anyone here running full auto-posting, or do you still approve every clip by hand?",
        "it isn't live yet, so i can't test it",
    ],
)
def test_clean_replies_pass(text: str) -> None:
    assert detect_slop(text, "en") == []


def test_em_dash_only_for_english() -> None:
    assert "em_dash" in {h.code for h in detect_slop("gas — cheap", "en")}
    assert "em_dash" in {h.code for h in detect_slop("gas – cheap", "en")}
    assert "em_dash" not in {h.code for h in detect_slop("газ — дешёвый", "ru")}


def test_lang_guessed_from_text_when_unknown() -> None:
    assert "em_dash" not in {h.code for h in detect_slop("газ — дешёвый и быстрый", None)}
    assert "em_dash" in {h.code for h in detect_slop("gas — cheap and fast", None)}


def test_curly_apostrophe_counts() -> None:
    assert "binary_contrast" in {
        h.code for h in detect_slop("it isn’t the model. it’s the eval.", "en")
    }


def test_banned_word_whole_words_only() -> None:
    assert detect_slop("robustness tests are flaky", "en") == []


def test_config_is_file_driven(tmp_path: Path) -> None:
    cfg = tmp_path / "slop.toml"
    cfg.write_text(
        '[en]\nem_dash = false\nwords = ["frobnicate"]\n'
        '[[en.patterns]]\ncode = "custom"\nmessage = "свой шаблон"\nregex = "\\\\bzap\\\\b"\n',
        encoding="utf-8",
    )
    config = load_slop_config(cfg)
    assert isinstance(config, SlopConfig)
    assert {h.code for h in detect_slop("zap — we frobnicate", "en", config)} == {
        "custom",
        "banned_word",
    }
    assert detect_slop("the real trap", "en", config) == []


def test_default_config_lives_in_config_dir() -> None:
    config = load_slop_config()
    assert config.lang("en").patterns and config.lang("en").words
    assert config.lang("en").em_dash is True
    assert config.lang("ru").em_dash is False


# ── правило линтера ─────────────────────────────────────────────────────────────────────


def lint(text: str, lang: str | None = "en", fmt: str = "reply") -> LintReport:
    return Linter().run(LintDraft(text=text, format=fmt), LintContext(lang=lang))


def test_rule_in_default_linter_and_blocks() -> None:
    report = lint("tbh that range isn't retirement money. it's walk-away money.")
    found = [f for f in report.findings if f.rule == "ai_slop"]
    assert found and all(f.level == "error" for f in found)
    assert "isn't retirement money" in found[0].fragment
    assert not report.ok


def test_rule_silent_on_clean_reply() -> None:
    report = lint("x402 stuff looks like memes for now but the tech underneath works")
    assert [f for f in report.findings if f.rule == "ai_slop"] == []


def test_lint_slop_turns_into_rewrite_instruction() -> None:
    report = lint("the real cooking is quiet")
    decision = decide(report, None, Thresholds())
    assert decision.outcome == "rewrite"
    lines = to_rewrite_instructions(decision)
    line = next(ln for ln in lines if "the real cooking" in ln)
    assert "AI" in line or "шаблон" in line


# ── судья ───────────────────────────────────────────────────────────────────────────────


def test_judge_has_slop_question() -> None:
    q = QUESTIONS_GATE2["ai_slop"]
    assert isinstance(q, ChoiceQ)
    assert tuple(q.options) == tuple(SLOP_OPTIONS)
    assert next(iter(q.options)) == "none"
    assert {"binary_contrast", "the_real_x", "kicker", "faux_insight"} <= set(q.options)
    assert "quote" in q.instructions


def _slop_answer(choice: str, conf: float, quote: str = "") -> ChoiceA:
    return ChoiceA(choice=choice, confidence=conf, probabilities={choice: conf}, quote=quote)


def test_verdict_without_slop_answer_defaults_to_none() -> None:
    v = JudgeVerdict.from_verdict(Verdict(answers=judge_answers(), judge="j"))
    assert v.ai_slop == "none" and v.ai_slop_quote == ""


def test_judge_slop_triggers_rewrite_with_quote() -> None:
    ans = judge_answers(ai_slop=_slop_answer("kicker", 0.9, "boring until the day it saves"))
    v = JudgeVerdict.from_verdict(Verdict(answers=ans, judge="j"))
    assert (v.ai_slop, v.ai_slop_quote) == ("kicker", "boring until the day it saves")
    report = lint("fallback saved us twice this month")
    decision = decide(report, v, Thresholds())
    assert decision.outcome == "rewrite"
    reason = next(r for r in decision.reasons if r.code == "ai_slop")
    assert reason.source == "judge" and reason.fragment == "boring until the day it saves"
    lines = to_rewrite_instructions(decision)
    assert any("boring until the day it saves" in ln for ln in lines)


def test_unsure_judge_slop_is_ignored() -> None:
    ans = judge_answers(ai_slop=_slop_answer("kicker", 0.3, "x"))
    v = JudgeVerdict.from_verdict(Verdict(answers=ans, judge="j"))
    assert decide(lint("fallback saved us twice this month"), v, Thresholds()).outcome == "pass"


def test_old_stored_verdict_still_loads() -> None:
    v = JudgeVerdict.from_verdict(Verdict(answers=judge_answers(), judge="j"))
    raw = v.model_dump(mode="json")
    for key in ("ai_slop", "ai_slop_quote", "ai_slop_confidence"):
        raw.pop(key, None)
    assert JudgeVerdict.model_validate(raw).ai_slop == "none"


def test_fallback_judge_keeps_quote() -> None:
    questions = {"ai_slop": QUESTIONS_GATE2["ai_slop"]}
    text = (
        '{"answers": {"ai_slop": {"choice": "the_real_x", "confidence": 0.8,'
        ' "quote": "the real trap"}}}'
    )
    ans = parse_answers(text, questions)["ai_slop"]
    assert isinstance(ans, ChoiceA) and ans.quote == "the real trap"
    plain = parse_answers('{"answers": {"ai_slop": {"choice": "none"}}}', questions)["ai_slop"]
    assert isinstance(plain, ChoiceA) and plain.quote == ""


def test_fallback_prompt_mentions_quote() -> None:
    assert '"quote"' in (PACKAGED_DIR / "judge.fallback.md").read_text(encoding="utf-8")


# ── промпты писателя ────────────────────────────────────────────────────────────────────

WRITER_PROMPTS = ("writer.draft.v1.md", "writer.rewrite.v1.md", "writer.trend_tail.write.v1.md")


@pytest.mark.parametrize("name", WRITER_PROMPTS)
def test_writer_prompts_carry_no_slop_rules(name: str) -> None:
    text = (PACKAGED_DIR / name).read_text(encoding="utf-8")
    system = text.split("@@user")[0]
    assert "no-ai-slop" in system and "MIT" in system
    for marker in ("em dash", "isn't X", "the real", "кикер"):
        assert marker in system, marker


def test_owner_reply_rule_kept() -> None:
    text = (PACKAGED_DIR / "writer.draft.v1.md").read_text(encoding="utf-8")
    assert "рядовой пользователь" in text and "спасибо, что поделился" in text
