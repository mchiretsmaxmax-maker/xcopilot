"""Строгие ворота для ответов (ветка feat/reply-gate-strict).

Решения владельца 19.11.2026 для format reply и quote (посты — как было):
1. Переписывать до показа, даже когда судья «не уверен»: ai_slop с уверенностью ≥ 0.4, hook ≤ 1,
   voice_match / ai_smell на пороге при judge_unsure. Пороги — в `config/thresholds.toml`.
2. «Не советуй автору»: явный императив-совет в конце ловит детектор (код `advice`, только
   reply/quote), мягкий — судья (код ai_slop `advice`). Правило — в промптах писателя.
3. Судья для ответов — `deepseek-v4-pro` (purpose `draft.judge.reply`), для постов — прежний.
"""

from __future__ import annotations

import tomllib
import uuid
from typing import Any

import pytest

from tests.quality_stand import judge_answers
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PACKAGED_DIR
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import ChoiceA, NoulA, ScoreA, Verdict
from xcopilot.llm.settings_paths import config_dir
from xcopilot.quality.instructions import to_rewrite_instructions
from xcopilot.quality.judge import (
    PURPOSE,
    REPLY_PURPOSE,
    SLOP_OPTIONS,
    JudgeVerdict,
    judge,
    purpose_for,
)
from xcopilot.quality.lint.engine import LintContext, LintDraft, Linter, LintReport
from xcopilot.quality.lint.slop import detect_slop
from xcopilot.quality.policy import Thresholds, decide
from xcopilot.retrieval.context_pack import ContextPack, TaskSpec

REPLY = (
    "streamer viewers follow the streamer. when the sponsorship ends and he moves to the next"
    " casino, a good chunk of your retained users leave with him. price that into the deal length"
)


def scale(score: float, conf: float) -> ScoreA:
    return ScoreA(score=score, confidence=conf, probabilities={round(score): conf})


def slop(choice: str, conf: float, quote: str = "") -> ChoiceA:
    return ChoiceA(choice=choice, confidence=conf, probabilities={choice: conf}, quote=quote)


def verdict(**over: Any) -> JudgeVerdict:
    return JudgeVerdict.from_verdict(Verdict(answers=judge_answers(**over), judge="j"))


def live_case() -> JudgeVerdict:
    """Черновик 01a1f31d из живой базы: судья «не уверен», kicker 0.45, hook 1, пороги ровно."""
    return verdict(
        voice_match=scale(2.0, 0.6),
        ai_smell=scale(2.0, 0.55),
        hook=scale(1.0, 0.55),
        ai_slop=slop("kicker", 0.45, "price that into the deal length"),
    )


def clean_report(text: str = "fair, we saw the same churn after our last sponsor left") -> Any:
    return Linter().run(LintDraft(text=text, format="reply"), LintContext(lang="en"))


# ── политика ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fmt", ["reply", "quote"])
def test_live_case_goes_to_rewrite_for_replies(fmt: str) -> None:
    d = decide(clean_report(), live_case(), Thresholds(), format=fmt)
    assert d.outcome == "rewrite"
    codes = {r.code for r in d.reasons}
    assert {"ai_slop", "weak_hook"} <= codes
    reason = next(r for r in d.reasons if r.code == "ai_slop")
    assert reason.fragment == "price that into the deal length"
    lines = to_rewrite_instructions(d)
    assert any("price that into the deal length" in ln for ln in lines), lines


def test_posts_keep_old_behaviour() -> None:
    d = decide(clean_report(), live_case(), Thresholds(), format="post")
    assert d.outcome == "needs_owner" and "judge_unsure" in d.flags
    d = decide(clean_report(), live_case(), Thresholds())  # формат не указан — как посты
    assert d.outcome == "needs_owner" and "judge_unsure" in d.flags


def test_unsure_flag_kept_on_strict_rewrite() -> None:
    d = decide(clean_report(), live_case(), Thresholds(), format="reply")
    assert "judge_unsure" in d.flags


def test_weak_hook_rewrites_reply_even_when_judge_sure() -> None:
    v = verdict(hook=scale(1.0, 0.9))
    assert decide(clean_report(), v, Thresholds(), format="reply").outcome == "rewrite"
    assert decide(clean_report(), v, Thresholds(), format="post").outcome == "pass"


def test_hook_above_max_passes_reply() -> None:
    v = verdict(hook=scale(2.0, 0.9))
    assert decide(clean_report(), v, Thresholds(), format="reply").outcome == "pass"


@pytest.mark.parametrize(("name", "code"), [("voice_match", "voice"), ("ai_smell", "ai_phrases")])
def test_score_on_threshold_with_unsure_judge_rewrites(name: str, code: str) -> None:
    v = verdict(**{name: scale(2.0, 0.5)}, hook=scale(2.0, 0.9))
    d = decide(clean_report(), v, Thresholds(), format="reply")
    assert d.outcome == "rewrite"
    assert code in {r.code for r in d.reasons}


def test_score_on_threshold_with_sure_judge_passes() -> None:
    v = verdict(voice_match=scale(2.0, 0.9), ai_smell=scale(2.0, 0.9), hook=scale(2.0, 0.9))
    assert decide(clean_report(), v, Thresholds(), format="reply").outcome == "pass"


def test_score_below_threshold_rewrites_even_if_unsure() -> None:
    v = verdict(ai_smell=scale(1.0, 0.5), hook=scale(2.0, 0.9))
    d = decide(clean_report(), v, Thresholds(), format="reply")
    assert d.outcome == "rewrite" and "ai_phrases" in {r.code for r in d.reasons}


def test_unsure_without_problems_still_goes_to_owner() -> None:
    v = verdict(voice_match=scale(3.0, 0.5), ai_smell=scale(3.0, 0.5), hook=scale(2.0, 0.5))
    d = decide(clean_report(), v, Thresholds(), format="reply")
    assert d.outcome == "needs_owner" and "judge_unsure" in d.flags


def test_low_confidence_slop_ignored_for_reply() -> None:
    v = verdict(hook=scale(2.0, 0.9), ai_slop=slop("kicker", 0.3, "x"))
    assert decide(clean_report(), v, Thresholds(), format="reply").outcome == "pass"


def test_risky_reply_still_goes_to_owner() -> None:
    v = verdict(risky=NoulA(p=0.9), hook=scale(1.0, 0.5))
    d = decide(clean_report(), v, Thresholds(), format="reply")
    assert d.outcome == "needs_owner" and "risky" in d.flags


def test_strict_thresholds_in_config() -> None:
    raw = tomllib.loads((config_dir() / "thresholds.toml").read_text(encoding="utf-8"))["gate"]
    assert raw["strict_formats"] == ["reply", "quote"]
    assert raw["strict_slop_confidence"] == 0.4
    assert raw["strict_hook_max"] == 1.0
    t = Thresholds.load()
    assert t.strict_formats == ["reply", "quote"] and t.strict_slop_confidence == 0.4
    assert t.max_rewrites == 2  # лимит кругов прежний


def test_account_can_override_strict_thresholds() -> None:
    t = Thresholds().with_overrides({"strict_hook_max": 0.0, "strict_formats": ["reply"]})
    v = verdict(hook=scale(1.0, 0.9))
    assert decide(clean_report(), v, t, format="reply").outcome == "pass"
    assert decide(clean_report(), live_case(), t, format="quote").outcome == "needs_owner"


# ── «не советуй автору»: детектор ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        (REPLY, "en"),
        ("solid launch. make sure the refund flow works before you scale ads", "en"),
        ("cool tool. start with one niche and grow from there.", "en"),
        ("the numbers look fine. consider a longer vesting cliff", "en"),
        ("nice thread. factor that into your pricing", "en"),
        ("good point. don't forget to check the bridge limits", "en"),
        ("стример уведёт аудиторию за собой. закладывайте это в срок контракта", "ru"),
        ("неплохо. обязательно проверьте лимиты моста перед запуском", "ru"),
    ],
)
def test_detects_advice_in_reply(text: str, lang: str) -> None:
    hits = detect_slop(text, lang, fmt="reply")
    hit = next((h for h in hits if h.code == "advice"), None)
    assert hit is not None, hits
    assert hit.fragment and hit.fragment.lower() in text.lower() and hit.message
    assert "advice" in {h.code for h in detect_slop(text, lang, fmt="quote")}


@pytest.mark.parametrize("fmt", ["post", "thread", None])
def test_advice_only_for_replies(fmt: str | None) -> None:
    assert "advice" not in {h.code for h in detect_slop(REPLY, "en", fmt=fmt)}
    assert "advice" not in {h.code for h in detect_slop(REPLY, "en")}


@pytest.mark.parametrize(
    "text",
    [
        "lol we priced that in last time and still lost half the users",
        "streamer audiences are rented, ours left the week the deal ended",
        "how long was the deal? ours churned in like two weeks",
        "i'd honestly just pay per depositor, flat fees burned us",
        "make sure is doing a lot of work in that sentence",
        "consider me sold, this is the first sponsor deal that made sense",
        "start of the cycle feels the same every time",
        "anyone here running full auto-posting, or do you still approve every clip by hand?",
    ],
)
def test_reactions_and_opinions_are_not_advice(text: str) -> None:
    assert "advice" not in {h.code for h in detect_slop(text, "en", fmt="reply")}


def test_lint_rule_flags_advice_in_reply_only() -> None:
    reply = Linter().run(LintDraft(text=REPLY, format="reply"), LintContext(lang="en"))
    found = [f for f in reply.findings if f.rule == "ai_slop"]
    assert any("price that into the deal length" in f.fragment for f in found), found
    assert not reply.ok
    post = Linter().run(LintDraft(text=REPLY, format="post"), LintContext(lang="en"))
    assert not any("price that" in f.fragment for f in post.findings if f.rule == "ai_slop")


def test_advice_lint_becomes_rewrite_instruction() -> None:
    report: LintReport = Linter().run(LintDraft(text=REPLY, format="reply"), LintContext(lang="en"))
    lines = to_rewrite_instructions(decide(report, None, Thresholds(), format="reply"))
    assert any("price that into the deal length" in ln for ln in lines), lines


# ── судья ───────────────────────────────────────────────────────────────────────────────


def test_judge_has_advice_option() -> None:
    assert "advice" in SLOP_OPTIONS
    label = SLOP_OPTIONS["advice"] or ""
    assert "совет" in label


def test_judge_advice_answer_parsed_and_rewrites_reply() -> None:
    v = verdict(hook=scale(2.0, 0.9), ai_slop=slop("advice", 0.5, "price that in"))
    assert v.ai_slop == "advice"
    d = decide(clean_report(), v, Thresholds(), format="reply")
    assert d.outcome == "rewrite"
    assert any("price that in" in ln for ln in to_rewrite_instructions(d))


def test_purpose_by_format() -> None:
    assert PURPOSE == "draft.judge"
    assert REPLY_PURPOSE == "draft.judge.reply"
    assert purpose_for("reply") == purpose_for("quote") == REPLY_PURPOSE
    assert purpose_for("post") == purpose_for("thread") == PURPOSE


@pytest.mark.parametrize(("fmt", "purpose"), [("reply", REPLY_PURPOSE), ("post", PURPOSE)])
async def test_judge_uses_purpose_of_format(fmt: str, purpose: str) -> None:
    seen: list[str] = []

    class Port:
        async def decide(self, **kw: Any) -> Verdict:
            seen.append(kw["purpose"])
            return Verdict(answers=judge_answers(), judge="j")

    pack = ContextPack(task=TaskSpec(format=fmt, lang="en", topic="t"), budget_tokens=6000)
    await judge(LintDraft(text="x", format=fmt), pack, port=Port(), account_id=uuid.uuid4())
    assert seen == [purpose]


# ── модели и цены ───────────────────────────────────────────────────────────────────────


def test_reply_judge_routed_to_v4_pro() -> None:
    judge_rc = ModelsConfig.load().roles[Role.JUDGE]
    assert judge_rc.model == "deepseek-flash"
    assert judge_rc.for_purpose(REPLY_PURPOSE).model == "deepseek-v4-pro"
    assert judge_rc.for_purpose(REPLY_PURPOSE).provider == judge_rc.provider
    assert judge_rc.for_purpose(PURPOSE) == judge_rc
    assert judge_rc.for_purpose("anything.else") == judge_rc


def test_v4_pro_has_own_price() -> None:
    table = PricingTable.load()
    pro = table.lookup("deepseek", "deepseek-v4-pro")
    assert pro != table.lookup("deepseek", "default")
    assert pro.input > 0 and pro.output > pro.input
    assert pro.peak_input is not None and pro.peak_output is not None


# ── промпты писателя ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["writer.draft.v1.md", "writer.rewrite.v1.md"])
def test_no_advice_rule_in_writer_prompts(name: str) -> None:
    text = (PACKAGED_DIR / name).read_text(encoding="utf-8")
    assert "Не советуй автору поста" in text
    for marker in ("price that in", "make sure", "реакция", "мнение", "свой опыт"):
        assert marker in text, marker
    assert "Без AI-шаблонов" in text  # блок no-ai-slop на месте


def test_owner_reply_wording_kept() -> None:
    text = (PACKAGED_DIR / "writer.draft.v1.md").read_text(encoding="utf-8")
    assert "Пиши прямо, просто и в разговорном стиле." in text
    assert "Пусть это звучит так, будто ты просто с кем-то болтаешь" in text
