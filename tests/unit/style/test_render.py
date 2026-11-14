"""Показ паспорта в Telegram и экспорт .md (ТЗ этапа 8, задача 7)."""

from __future__ import annotations

from xcopilot.style.passport import (
    Evidence,
    Example,
    FormatShare,
    HardRules,
    Lexicon,
    Structure,
    StylePassport,
)
from xcopilot.style.render_md import TG_LIMIT, export_md, render

SECTIONS = [
    "Описание",
    "Тон",
    "Лексика",
    "Структура",
    "Форматы",
    "Жёсткие правила",
    "Фирменные приёмы",
    "Примеры",
    "Анти-примеры",
    "Доказательства",
]


def full_passport(examples: int = 1) -> StylePassport:
    return StylePassport(
        summary="Спокойный технарь, пишет коротко.",
        tone="спокойный, точный",
        lexicon=Lexicon(use=["блобы", "роллап"], avoid=["революционный"]),
        structure=Structure(
            length="до 200 знаков",
            line_breaks="без переносов",
            openings=["факт без вступления"],
            endings=["без вопроса"],
        ),
        formats=[FormatShare(format="short", share=0.8), FormatShare(format="thread", share=0.2)],
        hard_rules=HardRules(casing="lower", emoji="none", banned_phrases=["delve"]),
        signature_patterns=["цифра в первой строке"],
        examples=[
            Example(chunk_id=f"c{i}", text=f"пример номер {i} " * 5) for i in range(examples)
        ],
        anti_examples=["Давайте погрузимся в мир блокчейна!"],
        evidence=[Evidence(claim="строчные буквы", chunk_ids=["c0"])],
    )


def test_render_splits_messages_over_4096() -> None:
    parts = render(full_passport(examples=400), version=3)
    assert len(parts) > 1
    assert all(0 < len(p) <= TG_LIMIT for p in parts)
    joined = "\n".join(parts)
    assert "пример номер 399" in joined  # ничего не потеряно при нарезке
    assert render(full_passport(), version=1)  # короткий паспорт тоже отдаётся


def test_export_md_contains_all_sections() -> None:
    md = export_md(full_passport(), version=3)
    assert md.startswith("# ")
    for section in SECTIONS:
        assert f"## {section}" in md, section
    assert "lower" in md or "строчные" in md.lower()
    assert "delve" in md
    assert "v3" in md
