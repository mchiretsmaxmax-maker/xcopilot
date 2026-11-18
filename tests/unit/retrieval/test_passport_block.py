"""«Мои правила» владельца идут писателю отдельным разделом блока паспорта (feat/passport-file)."""

from __future__ import annotations

from xcopilot.brain.schemas import TaskSpec
from xcopilot.retrieval.context_pack import ContextPack
from xcopilot.style.passport import StylePassport


def pack(passport: StylePassport) -> ContextPack:
    body = passport.model_dump(mode="json")
    return ContextPack(
        task=TaskSpec(format="short", lang="ru", topic="блобы и l2"),
        budget_tokens=3000,
        passport_version=7,
        passport=body,
        hard_rules=body["hard_rules"],
    )


def passport_section(p: ContextPack) -> str:
    return dict(p.sections())["passport"]


def test_owner_rules_are_separate_mandatory_section() -> None:
    rules = "Никогда не пиши про цену токена.\nОтвечай коротко."
    text = passport_section(pack(StylePassport(tone="спокойный", owner_rules=rules, label="x")))
    assert "# Правила владельца (обязательны)\n" + rules in text
    head = text.split("# Правила владельца")[0]
    assert "owner_rules" not in head  # не дублируется внутри JSON
    for meta in ("label", "origin", "owner_set"):  # служебные поля писателю не нужны
        assert f'"{meta}"' not in text


def test_no_owner_rules_no_section() -> None:
    text = passport_section(pack(StylePassport(tone="спокойный")))
    assert "Правила владельца" not in text
