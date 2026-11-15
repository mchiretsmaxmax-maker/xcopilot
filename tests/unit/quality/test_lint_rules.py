"""Линтер этапа 11: правила v1, автоисправления, foreign_similarity и знания класса C (v3.1)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from xcopilot.quality.lint.engine import (
    ForeignText,
    LintContext,
    LintDraft,
    Linter,
    LintReport,
    SoftRule,
)
from xcopilot.quality.lint.numbers import extract_numbers, is_grounded, normalize
from xcopilot.style.passport import HardRules


def lint(text: str, ctx: LintContext | None = None, *, fmt: str = "post",
         parts: tuple[str, ...] = ()) -> LintReport:  # fmt: skip
    return Linter().run(LintDraft(text=text, format=fmt, parts=parts), ctx or LintContext())


def rules_hit(report: LintReport, rule: str) -> list[str]:
    return [f.level for f in report.findings if f.rule == rule and not f.autofixed]


# ── длина, регистр, эмодзи, хэштеги, ссылки ─────────────────────────────────────────────


def test_length_rule_uses_weighted_len() -> None:
    url = "https://example.com/" + "a" * 120  # по X ссылка весит 23
    text = "к" * 200 + " " + url
    assert len(text) > 280
    assert rules_hit(lint(text), "length") == []
    wide = "字" * 150  # широкие символы весят 2 → 300
    assert len(wide) < 280
    assert rules_hit(lint(wide, LintContext(lang=None)), "length") == ["error"]
    tight = LintContext(hard_rules=HardRules(max_len=100))
    assert rules_hit(lint("слово " * 30, tight), "length") == ["error"]


def test_casing_lower_rule_and_autofix() -> None:
    ctx = LintContext(hard_rules=HardRules(casing="lower"))
    ctx = LintContext(hard_rules=HardRules(casing="lower"), user_input="упомяни @Vitalik")
    report = lint("Блобы сделали L2 дешевле. Смотри https://Example.com/Path и @Vitalik", ctx)
    assert report.ok
    assert "casing" in report.autofixed
    assert report.text.startswith("блобы сделали l2 дешевле")
    assert "https://Example.com/Path" in report.text  # ссылки и упоминания не трогаем
    assert "@Vitalik" in report.text
    fixed = [f for f in report.findings if f.rule == "casing"]
    assert fixed and all(f.autofixed for f in fixed)
    assert lint("Как есть", LintContext()).autofixed == []


def test_emoji_none_rule() -> None:
    ctx = LintContext(hard_rules=HardRules(emoji="none"))
    assert rules_hit(lint("газ дешевеет 🚀", ctx), "emoji") == ["error"]
    assert rules_hit(lint("газ дешевеет", ctx), "emoji") == []
    rare = LintContext(hard_rules=HardRules(emoji="rare"))
    assert rules_hit(lint("газ 🚀 дешевеет", rare), "emoji") == []
    assert rules_hit(lint("газ 🚀 дешевеет 🔥", rare), "emoji") == ["error"]


def test_hashtags_rule_and_autofix() -> None:
    ctx = LintContext(hard_rules=HardRules(hashtags="none"))
    report = lint("роллапы съедают рынок #crypto #l2", ctx)
    assert report.ok and "hashtags" in report.autofixed
    assert "#" not in report.text
    assert report.text == "роллапы съедают рынок"
    rare = LintContext(hard_rules=HardRules(hashtags="rare"))
    report = lint("роллапы #crypto съедают #l2 рынок #eth", rare)
    assert report.ok and report.text.count("#") == 1 and "#crypto" in report.text
    assert lint("#crypto ок", LintContext()).autofixed == []


def test_links_rule() -> None:
    ctx = LintContext(hard_rules=HardRules(links="none"))
    assert rules_hit(lint("читай https://docs.example.com", ctx), "links") == ["error"]
    assert rules_hit(lint("читай документацию", ctx), "links") == []
    rare = LintContext(hard_rules=HardRules(links="rare"))
    two = "раз https://a.example.com и два http://b.example.com"
    assert rules_hit(lint(two, rare), "links") == ["error"]
    assert rules_hit(lint("раз https://a.example.com", rare), "links") == []


# ── запреты и шаблоны AI ───────────────────────────────────────────────────────────────


def test_banned_phrase_case_insensitive_ru_en() -> None:
    ctx = LintContext(hard_rules=HardRules(banned_phrases=["К луне", "game changer"]))
    report = lint("Этот апгрейд — GAME CHANGER, летим к ЛУНЕ", ctx)
    hits = [f for f in report.findings if f.rule == "banned_phrases"]
    assert {f.fragment.lower() for f in hits} == {"game changer", "к луне"}
    assert all(f.level == "error" for f in hits)
    assert rules_hit(lint("спокойный пост", ctx), "banned_phrases") == []


def test_global_banned_phrases_applied() -> None:
    ctx = LintContext(global_banned=("в современном мире",))
    report = lint("В современном мире газ дешевеет", ctx)
    assert rules_hit(report, "banned_phrases") == ["error"]


def test_ai_pattern_not_x_but_y_ru_en() -> None:
    ru = lint("это не просто апгрейд, а новая эпоха для сети", LintContext(lang="ru"))
    assert "error" in rules_hit(ru, "ai_patterns")
    found = [f for f in ru.findings if f.rule == "ai_patterns"]
    assert any("не просто апгрейд, а" in f.fragment for f in found)
    en = lint("It's not just an upgrade, it's a revolution for the chain", LintContext(lang="en"))
    assert "error" in rules_hit(en, "ai_patterns")
    en2 = lint("this is not a bug, but a feature of the design", LintContext(lang="en"))
    assert "error" in rules_hit(en2, "ai_patterns")
    plain = lint("не гонись за хайпом, читай документацию", LintContext(lang="ru"))
    assert rules_hit(plain, "ai_patterns") == []


def test_ai_pattern_dash_overuse() -> None:
    text = "газ дешевеет — и это важно — потому что сеть растёт — а пользователи — остаются"
    assert "error" in rules_hit(lint(text, LintContext(lang="ru")), "ai_patterns")
    one = "газ дешевеет — и это хороший знак для всей сети"
    assert rules_hit(lint(one, LintContext(lang="ru")), "ai_patterns") == []


# ── цифры ──────────────────────────────────────────────────────────────────────────────


def test_number_grounding_passes_when_in_facts() -> None:
    facts = ("TVL сети вырос до 1.2M за квартал",)
    for text in ("tvl уже 1,200,000", "tvl уже 1.2 млн", "tvl уже 1.2M", "tvl уже 1,2 млн"):
        assert rules_hit(lint(text, LintContext(facts=facts)), "number_grounding") == [], text
    assert normalize("1.2M") == normalize("1,200,000") == normalize("1.2 млн") == Decimal(1200000)
    token = extract_numbers("уже 1.2 млн")[0]
    assert is_grounded(token, facts, "")


def test_number_grounding_fails_on_unsupported_percent() -> None:
    facts = ("комиссии упали на 90% после апгрейда",)
    report = lint("комиссии упали на 95% после апгрейда", LintContext(facts=facts))
    hits = [f for f in report.findings if f.rule == "number_grounding"]
    assert [f.level for f in hits] == ["error"]
    assert "95%" in hits[0].fragment
    ok = lint("комиссии упали на 90%", LintContext(facts=facts))
    assert rules_hit(ok, "number_grounding") == []
    # 90 без процента в фактах не подтверждает 90%
    plain = lint("рост 90%", LintContext(facts=("в сети 90 валидаторов",)))
    assert rules_hit(plain, "number_grounding") == ["error"]


def test_number_grounding_accepts_numbers_from_user_input() -> None:
    ctx = LintContext(user_input="напиши про то, что газ подешевел в 40 раз")
    assert rules_hit(lint("газ подешевел в 40 раз", ctx), "number_grounding") == []
    assert rules_hit(lint("газ подешевел в 45 раз", ctx), "number_grounding") == ["error"]


def test_number_grounding_ignores_list_numbering() -> None:
    text = "что я понял:\n1. блобы дешёвые\n2) роллапы растут\n3. мосты закрываются"
    assert rules_hit(lint(text), "number_grounding") == []
    thread_marks = "блобы дешёвые 1/3"
    assert rules_hit(lint(thread_marks), "number_grounding") == []


# ── упоминания, дубли, язык, тред ──────────────────────────────────────────────────────


def test_mention_not_in_input_flagged() -> None:
    ctx = LintContext(user_input="ответь @alice про газ")
    report = lint("@alice согласен, а @bob ошибается", ctx)
    hits = [f for f in report.findings if f.rule == "mention_safety"]
    assert [f.fragment for f in hits] == ["@bob"]
    assert hits[0].level == "error"


def test_duplicate_of_recent_own_post() -> None:
    own = ("блобы сделали l2 дешевле в десять раз, и это видно по комиссиям",)
    ctx = LintContext(own_recent=own)
    near = "блобы сделали l2 дешевле в десять раз, и это видно по комиссиям!"
    assert rules_hit(lint(near, ctx), "duplicate_of_recent") == ["error"]
    other = "старый мост закрылся без шума, ликвидность ушла в новые сети"
    assert rules_hit(lint(other, ctx), "duplicate_of_recent") == []


def test_language_mismatch() -> None:
    en_text = "gas fees dropped a lot after the upgrade and users stayed on the network"
    assert rules_hit(lint(en_text, LintContext(lang="ru")), "language") == ["error"]
    ru_text = "газ подешевел после апгрейда, а пользователи остались в сети, $ETH и l2"
    assert rules_hit(lint(ru_text, LintContext(lang="ru")), "language") == []
    assert rules_hit(lint(en_text, LintContext(lang="en")), "language") == []


def test_thread_part_over_limit() -> None:
    parts = ("первая часть про газ", "в" * 300, "третья часть, вывод")
    report = lint("\n\n".join(parts), fmt="thread", parts=parts)
    hits = [f for f in report.findings if f.rule == "thread_structure"]
    assert [f.level for f in hits] == ["error"]
    assert "2" in hits[0].message
    ok = ("раз", "два", "три")
    assert rules_hit(lint("\n\n".join(ok), fmt="thread", parts=ok), "thread_structure") == []
    few = ("раз", "два")
    assert rules_hit(lint("раз\n\nдва", fmt="thread", parts=few), "thread_structure") == ["error"]


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(text=st.text(max_size=600), lang=st.sampled_from([None, "ru", "en"]))
def test_linter_never_raises_on_arbitrary_text(text: str, lang: str | None) -> None:
    ctx = LintContext(
        hard_rules=HardRules(casing="lower", emoji="none", hashtags="rare", links="none"),
        lang=lang,
        facts=("1.2M", "90%"),
        own_recent=("свой пост",),
        foreign=(ForeignText("чужой пост про газ"),),
        class_c=(SoftRule(uuid.uuid4(), "Не пиши «экосистема»", ("экосистема",)),),
    )
    report = Linter().run(LintDraft(text=text, format="thread", parts=(text,)), ctx)
    assert isinstance(report, LintReport)
    assert all(f.rule != "internal" for f in report.findings)


# ── v3.1: чужой материал и знания класса C ─────────────────────────────────────────────

FOREIGN = (
    "газ в эфире подешевел в десять раз после апгрейда, и теперь роллапы забирают всех "
    "новых пользователей у старых сетей"
)


def test_foreign_similarity_blocks_near_copy() -> None:
    ctx = LintContext(foreign=(ForeignText(FOREIGN, uuid.uuid4()),))
    near = FOREIGN.replace("десять", "10 ").replace("всех", "почти всех")
    report = lint(near, ctx)
    assert rules_hit(report, "foreign_similarity") == ["error"]
    own_take = "у меня комиссии стали копеечными, перенёс кошелёк на l2 и забыл про мосты"
    assert rules_hit(lint(own_take, ctx), "foreign_similarity") == []


def test_eight_word_overlap_with_foreign_material_blocked() -> None:
    ctx = LintContext(foreign=(ForeignText(FOREIGN),))
    eight = (
        "за месяц я понял одно: роллапы забирают всех новых пользователей у старых сетей, пока я"
        " спокойно читаю документацию по мостам и не спешу"
    )  # сходство целиком ниже 0.6 — ловит именно общая последовательность из 8 слов
    report = lint(eight, ctx)
    hits = [f for f in report.findings if f.rule == "foreign_similarity"]
    assert [f.level for f in hits] == ["error"]
    assert "роллапы забирают всех новых пользователей у старых сетей" in hits[0].fragment.lower()
    seven = (
        "за месяц я понял одно: роллапы забирают всех новых пользователей у меня в ленте, пока я"
        " спокойно читаю документацию по мостам и не спешу"
    )
    assert rules_hit(lint(seven, ctx), "foreign_similarity") == []


def test_own_material_overlap_handled_by_duplicate_rule_not_foreign() -> None:
    own = "блобы сделали l2 дешевле в десять раз, и это видно по комиссиям за неделю"
    ctx = LintContext(own_recent=(own,), foreign=(ForeignText("совсем другой чужой текст"),))
    report = lint(own, ctx)
    assert rules_hit(report, "duplicate_of_recent") == ["error"]
    assert rules_hit(report, "foreign_similarity") == []


def test_class_c_items_are_warnings_not_errors() -> None:
    item = uuid.uuid4()
    rule = SoftRule(item, "Не используй слово «экосистема» без конкретики", ("экосистема",))
    report = lint("вся экосистема растёт", LintContext(class_c=(rule,)))
    hits = [f for f in report.findings if f.rule == "knowledge_c"]
    assert [f.level for f in hits] == ["warn"]
    assert hits[0].fragment.lower() == "экосистема"
    assert report.ok
    assert hits[0] in report.warnings
