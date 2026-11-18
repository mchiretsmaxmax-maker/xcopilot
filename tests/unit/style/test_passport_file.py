"""Паспорт как файл (feat/passport-file): экспорт ↔ разбор, ошибки, «Мои правила», diff."""

from __future__ import annotations

import pytest

from xcopilot.style.diff import diff_lines
from xcopilot.style.parse_md import PassportParseError, parse_md
from xcopilot.style.passport import (
    Evidence,
    Example,
    FormatShare,
    HardRules,
    Lexicon,
    Structure,
    StylePassport,
)
from xcopilot.style.render_md import export_md

SECTIONS = [
    "Тон",
    "Как пишу",
    "Слова: использую / избегаю",
    "Фирменные приёмы",
    "Мои правила",
    "Примеры",
    "Антипримеры",
    "Жёсткие правила",
]


def full() -> StylePassport:
    return StylePassport(
        summary="Спокойный технарь, пишет коротко.",
        tone="спокойный, точный\nбез восклицаний",
        lexicon=Lexicon(use=["блобы", "роллап, l2"], avoid=["революционный"]),
        structure=Structure(
            length="до 200 знаков",
            line_breaks="без переносов",
            openings=["факт без вступления", "цифра"],
            endings=["без вопроса"],
        ),
        formats=[FormatShare(format="short", share=0.8)],
        hard_rules=HardRules(
            casing="lower",
            emoji="rare",
            hashtags="none",
            links="free",
            max_len=500,
            banned_phrases=["delve", "в мире, где"],
        ),
        signature_patterns=["цифра в первой строке"],
        examples=[
            Example(chunk_id="c1", text="блобы сделали l2 дешевле"),
            Example(chunk_id="c2", text="первая строка\n\nвторая строка после пустой"),
        ],
        anti_examples=["Давайте погрузимся в мир блокчейна!"],
        evidence=[Evidence(claim="строчные буквы", chunk_ids=["c1"])],
        owner_rules="Никогда не пиши про цену токена.\nОтвечай коротко.",
        rough=True,
    )


def test_export_has_human_sections() -> None:
    md = export_md(full(), version=5)
    for title in SECTIONS:
        assert f"\n# {title}\n" in md, title
    assert "регистр: строчные" in md
    assert "эмодзи: редко" in md
    assert "длина: 500" in md
    assert "Никогда не пиши про цену токена." in md


def test_round_trip_same_passport() -> None:
    p = full()
    assert parse_md(export_md(p, version=5), base=p).passport == p


def test_round_trip_empty_and_default_passport() -> None:
    p = StylePassport()
    assert parse_md(export_md(p), base=p).passport == p


def test_round_trip_without_base_keeps_editable_fields() -> None:
    p = full()
    got = parse_md(export_md(p)).passport
    for field in (
        "summary",
        "tone",
        "lexicon",
        "structure",
        "hard_rules",
        "signature_patterns",
        "anti_examples",
        "owner_rules",
    ):
        assert getattr(got, field) == getattr(p, field), field
    assert [e.text for e in got.examples] == [e.text for e in p.examples]


def test_owner_edit_is_parsed() -> None:
    p = full()
    md = export_md(p).replace("эмодзи: редко", "эмодзи: нет")
    md = md.replace("Отвечай коротко.", "Отвечай коротко.\nБез сленга.")
    got = parse_md(md, base=p).passport
    assert got.hard_rules.emoji == "none"
    assert got.owner_rules.splitlines()[-1] == "Без сленга."


def test_banned_phrases_plain_comma_list() -> None:
    md = export_md(StylePassport())
    md = md.replace("запрещённые фразы: —", "запрещённые фразы: a, b,  c")
    assert parse_md(md).passport.hard_rules.banned_phrases == ["a", "b", "c"]


def test_bad_hard_rule_value_names_line_and_allowed_values() -> None:
    md = export_md(full())
    lines = md.split("\n")
    n = next(i for i, line in enumerate(lines, 1) if line.startswith("эмодзи:"))
    lines[n - 1] = "эмодзи: иногда"
    with pytest.raises(PassportParseError) as err:
        parse_md("\n".join(lines))
    text = str(err.value)
    assert f"Строка {n}" in text
    assert "иногда" in text
    for allowed in ("нет", "редко", "свободно"):
        assert allowed in text
    assert err.value.line == n


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("длина: много", "число"),
        ("длина: 0", "число"),
        ("регистр: громкий", "любой"),
        ("цвет: синий", "регистр"),
        ("без двоеточия", "двоеточ"),
    ],
)
def test_other_hard_rule_errors(line: str, needle: str) -> None:
    md = export_md(StylePassport()) + "\n" + line
    with pytest.raises(PassportParseError) as err:
        parse_md(md)
    assert needle in str(err.value).lower()
    assert "Строка" in str(err.value)


def test_unknown_section_is_warning_not_error() -> None:
    md = export_md(full()) + "\n\n# Котики\nлюблю котиков\n"
    result = parse_md(md, base=full())
    assert result.passport == full()
    assert any("Котики" in w for w in result.warnings)


def test_empty_file_is_error() -> None:
    with pytest.raises(PassportParseError):
        parse_md("   \n")


def test_diff_lines_summary() -> None:
    old = full()
    new = old.model_copy(
        update={
            "tone": "другой",
            "owner_rules": old.owner_rules + "\nПравило три.\nПравило четыре.",
            "hard_rules": old.hard_rules.model_copy(update={"emoji": "none"}),
        }
    )
    lines = diff_lines(old, new)
    assert "Тон: изменён" in lines
    assert "Мои правила: +2" in lines
    assert "Жёсткие: эмодзи редко → нет" in lines
    assert diff_lines(old, old) == []


# --- feat/style-formats: правила по форматам ---------------------------------------------


def test_round_trip_with_format_rules() -> None:
    p = full().model_copy(
        update={
            "format_rules": {"reply": "Отвечай в одну строку.\nБез имён.", "quote": "Своя мысль."}
        }
    )
    md = export_md(p, version=5)
    for title in (
        "Правила для постов",
        "Правила для тредов",
        "Правила для ответов",
        "Правила для цитат",
    ):
        assert f"\n# {title}\n" in md, title  # пустые тоже выводятся
    assert parse_md(md, base=p).passport == p
    assert parse_md(md).passport.format_rules == p.format_rules


def test_format_rules_edit_is_parsed() -> None:
    md = export_md(StylePassport(), hints=False).replace(
        "# Правила для ответов\n\n—", "# Правила для ответов\n\nНе спорь."
    )
    got = parse_md(md).passport
    assert got.format_rules == {"reply": "Не спорь."}
