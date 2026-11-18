"""Краткий diff двух паспортов для владельца: «Тон: изменён; Мои правила: +2; Жёсткие: …»."""

from __future__ import annotations

from xcopilot.style.passport import RULE_NAMES, StylePassport
from xcopilot.style.render_md import CASING_MD, LEVEL_MD

RULE_TITLES = {
    "casing": "регистр",
    "emoji": "эмодзи",
    "hashtags": "хэштеги",
    "links": "ссылки",
    "max_len": "длина",
    "banned_phrases": "запрещённые фразы",
}


def rule_value(name: str, value: object) -> str:
    """Значение жёсткого правила по-русски: «строчные», «редко», «280»."""
    if name == "casing":
        return CASING_MD.get(str(value), str(value))
    if name in ("emoji", "hashtags", "links"):
        return LEVEL_MD.get(str(value), str(value))
    return str(value)


def _counts(title: str, old: list[str], new: list[str]) -> str | None:
    added = len([x for x in new if x not in old])
    removed = len([x for x in old if x not in new])
    if not added and not removed:
        return None if old == new else f"{title}: порядок изменён"
    parts = ([f"+{added}"] if added else []) + ([f"−{removed}"] if removed else [])
    return f"{title}: {' '.join(parts)}"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def hard_rule_changes(old: StylePassport, new: StylePassport) -> list[str]:
    """«эмодзи редко → нет», «+запрет «x»» — чем отличаются жёсткие правила."""
    out: list[str] = []
    for name in RULE_NAMES:
        a, b = getattr(old.hard_rules, name), getattr(new.hard_rules, name)
        if a != b:
            out.append(f"{RULE_TITLES[name]} {rule_value(name, a)} → {rule_value(name, b)}")
    old_bans = {p.casefold() for p in old.hard_rules.banned_phrases}
    new_bans = {p.casefold() for p in new.hard_rules.banned_phrases}
    out += [f"+запрет «{p}»" for p in new.hard_rules.banned_phrases if p.casefold() not in old_bans]
    out += [f"−запрет «{p}»" for p in old.hard_rules.banned_phrases if p.casefold() not in new_bans]
    return out


def diff_lines(old: StylePassport | None, new: StylePassport) -> list[str]:
    """Что поменялось для владельца; служебные поля (название, owner_set) не считаются."""
    if old is None:
        return ["первая версия"]
    out: list[str] = []
    if old.summary != new.summary:
        out.append("Описание: изменено")
    if old.tone != new.tone:
        out.append("Тон: изменён")
    if old.structure != new.structure:
        out.append("Как пишу: изменено")
    for line in (
        _counts("Слова «использую»", old.lexicon.use, new.lexicon.use),
        _counts("Слова «избегаю»", old.lexicon.avoid, new.lexicon.avoid),
        _counts("Фирменные приёмы", old.signature_patterns, new.signature_patterns),
        _counts("Мои правила", _lines(old.owner_rules), _lines(new.owner_rules)),
        _counts("Примеры", [e.text for e in old.examples], [e.text for e in new.examples]),
        _counts("Антипримеры", old.anti_examples, new.anti_examples),
    ):
        if line is not None:
            out.append(line)
    out += [f"Жёсткие: {c}" for c in hard_rule_changes(old, new)]
    return out


def diff_text(old: StylePassport | None, new: StylePassport) -> str:
    lines = diff_lines(old, new)
    return "\n".join(f"• {line}" for line in lines) if lines else "• без изменений"
