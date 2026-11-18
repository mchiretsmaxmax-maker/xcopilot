"""Разбор паспорта стиля из .md владельца — обратная операция к `render_md.export_md`.

Разделы — заголовки «# …». Неизвестный раздел — предупреждение; ошибка в «Жёстких правилах»
(машиночитаемый раздел) — `PassportParseError` с номером строки и допустимыми значениями.
Чего нет в файле (evidence, formats, rough, id кусков примеров, служебные поля версии),
берётся из паспорта-основы `base`; раздел, которого нет в файле, тоже остаётся как в основе.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from xcopilot.style.errors import StyleError
from xcopilot.style.passport import Example, HardRules, Lexicon, Structure, StylePassport
from xcopilot.style.render_md import CASING_MD, EMPTY, EXAMPLE_SEP, LEVEL_MD

OWNER_CHUNK = "owner"  # id «куска» для примера, который владелец вписал сам
_HEADING = re.compile(r"^#{1,2}\s+(.+?)\s*#*\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_KEY = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")

_SECTIONS = {
    "описание": "summary",
    "тон": "tone",
    "как пишу": "structure",
    "структура": "structure",
    "слова": "lexicon",
    "лексика": "lexicon",
    "фирменные приемы": "signature_patterns",
    "мои правила": "owner_rules",
    "примеры": "examples",
    "антипримеры": "anti_examples",
    "анти-примеры": "anti_examples",
    "жесткие правила": "hard_rules",
}
_TITLES = {
    "summary": "Описание",
    "tone": "Тон",
    "structure": "Как пишу",
    "lexicon": "Слова",
    "signature_patterns": "Фирменные приёмы",
    "owner_rules": "Мои правила",
    "examples": "Примеры",
    "anti_examples": "Антипримеры",
    "hard_rules": "Жёсткие правила",
}
_LEVELS = {v: k for k, v in LEVEL_MD.items()}
_CASINGS = {v: k for k, v in CASING_MD.items()} | {"только строчные": "lower"}
_RULE_KEYS = {
    "регистр": "casing",
    "эмодзи": "emoji",
    "хэштеги": "hashtags",
    "хештеги": "hashtags",
    "ссылки": "links",
    "длина": "max_len",
    "запрещенные фразы": "banned_phrases",
    "запреты": "banned_phrases",
}
_RULE_TITLES = "регистр, эмодзи, хэштеги, ссылки, длина, запрещённые фразы"


class PassportParseError(StyleError):
    """Файл паспорта не разобран: строка `line` и понятное объяснение."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"Строка {line}: {message}")
        self.line = line


@dataclass
class ParseResult:
    passport: StylePassport
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Section:
    title: str
    line: int  # номер строки заголовка
    body: list[tuple[int, str]] = field(default_factory=list)

    def text(self) -> str:
        """Тело раздела дословно, без пустых строк по краям; «—» — пусто."""
        lines = [t.rstrip() for _, t in self.body]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        text = "\n".join(lines)
        return "" if text.strip() == EMPTY else text


def _norm(title: str) -> str:
    return title.strip().lower().replace("ё", "е").split(":")[0].strip()


def _strip_comments(text: str) -> str:
    """Убрать <!-- … -->, сохранив переводы строк (номера строк в ошибках не съезжают)."""
    return _COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _split(text: str) -> list[_Section]:
    sections: list[_Section] = []
    for number, raw in enumerate(_strip_comments(text).split("\n"), start=1):
        heading = _HEADING.match(raw)
        if heading:
            sections.append(_Section(heading.group(1), number))
        elif sections:
            sections[-1].body.append((number, raw))
    return sections


def _item(line: str) -> str | None:
    """«- текст» → «текст»; пустая строка и «—» → None."""
    clean = line.strip()
    if clean.startswith(("- ", "* ")):
        clean = clean[2:].strip()
    return None if not clean or clean == EMPTY else clean


def _items(sec: _Section) -> list[str]:
    return [x for _, line in sec.body if (x := _item(line)) is not None]


def _labelled(sec: _Section, keys: dict[str, str], warnings: list[str]) -> dict[str, Any]:
    """Строки «Ключ: значение» и «Ключ:» со списком «- …» ниже."""
    out: dict[str, Any] = {}
    current: str | None = None
    for number, line in sec.body:
        if not line.strip():
            continue
        match = _KEY.match(line)
        is_item = line.lstrip().startswith(("- ", "* "))
        name = keys.get(_norm(match.group(1))) if match and not is_item else None
        if name is not None and match is not None:
            current = name
            value = match.group(2)
            out[name] = "" if value == EMPTY else value
            continue
        item = _item(line)
        if current is not None and item is not None:
            prev = out.get(current)
            if isinstance(prev, list):
                out[current] = [*prev, item]
            else:
                out[current] = [prev, item] if prev else [item]
        elif item is not None:
            warnings.append(f"Строка {number}: не понял «{line.strip()}» в разделе «{sec.title}»")
    return out


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _structure(sec: _Section, warnings: list[str]) -> Structure:
    keys = {
        "длина": "length",
        "переносы": "line_breaks",
        "начала": "openings",
        "зачины": "openings",
        "концовки": "endings",
    }
    data = _labelled(sec, keys, warnings)
    return Structure(
        length=str(data.get("length") or ""),
        line_breaks=str(data.get("line_breaks") or ""),
        openings=_as_list(data.get("openings")),
        endings=_as_list(data.get("endings")),
    )


def _lexicon(sec: _Section, warnings: list[str]) -> Lexicon:
    data = _labelled(sec, {"использую": "use", "избегаю": "avoid"}, warnings)
    return Lexicon(use=_as_list(data.get("use")), avoid=_as_list(data.get("avoid")))


def _blocks(sec: _Section) -> list[str]:
    blocks: list[list[str]] = [[]]
    for _, line in sec.body:
        if line.strip() == EXAMPLE_SEP:
            blocks.append([])
        else:
            blocks[-1].append(line.rstrip())
    out: list[str] = []
    for block in blocks:
        text = "\n".join(block).strip("\n")
        if text.strip() and text.strip() != EMPTY:
            out.append(text)
    return out


def _phrases(value: str) -> list[str]:
    if value in ("", EMPTY):
        return []
    quoted = re.findall(r"«(.+?)»", value)
    if quoted:
        return [q.strip() for q in quoted if q.strip()]
    return [p.strip() for p in value.split(",") if p.strip()]


def _hard_rules(sec: _Section) -> HardRules:
    data: dict[str, Any] = {}
    for number, raw in sec.body:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        match = _KEY.match(line)
        if match is None:
            raise PassportParseError(
                number,
                f"«{line}» — нет двоеточия. Пиши «правило: значение», например «эмодзи: нет»",
            )
        title, value = match.group(1), match.group(2)
        name = _RULE_KEYS.get(_norm(title))
        if name is None:
            raise PassportParseError(
                number, f"неизвестное правило «{title}». Можно: {_RULE_TITLES}"
            )
        value_n = value.lower().replace("ё", "е")
        if name == "casing":
            if value_n not in _CASINGS:
                raise PassportParseError(number, f"регистр «{value}» — можно: любой, строчные")
            data[name] = _CASINGS[value_n]
        elif name in ("emoji", "hashtags", "links"):
            if value_n not in _LEVELS:
                raise PassportParseError(
                    number, f"{title.strip().lower()} «{value}» — можно: нет, редко, свободно"
                )
            data[name] = _LEVELS[value_n]
        elif name == "max_len":
            if not value.isdigit() or not 1 <= int(value) <= 25_000:
                raise PassportParseError(
                    number, f"длина «{value}» — нужно целое число от 1 до 25000"
                )
            data[name] = int(value)
        else:
            data[name] = _phrases(value)
    return HardRules.model_validate(data)


def parse_md(text: str, base: StylePassport | None = None) -> ParseResult:
    """Паспорт из .md владельца; `base` — откуда взять то, чего в файле нет."""
    if not text.strip():
        raise PassportParseError(1, "файл пустой")
    sections = _split(text)
    warnings: list[str] = []
    base = base or StylePassport()
    known: dict[str, _Section] = {}
    for sec in sections:
        name = _SECTIONS.get(_norm(sec.title))
        if name is None:
            warnings.append(f"Строка {sec.line}: раздел «{sec.title}» не знаю — пропустил")
        elif name in known:
            warnings.append(f"Строка {sec.line}: раздел «{sec.title}» повторяется — взял первый")
        else:
            known[name] = sec
    if not known:
        raise PassportParseError(
            1, "не нашёл разделов паспорта. Нужны заголовки вида «# Тон», «# Жёсткие правила»"
        )
    update: dict[str, Any] = {}
    for name, sec in known.items():
        if name in ("summary", "tone", "owner_rules"):
            update[name] = sec.text()
        elif name == "structure":
            update[name] = _structure(sec, warnings)
        elif name == "lexicon":
            update[name] = _lexicon(sec, warnings)
        elif name == "signature_patterns":
            update[name] = _items(sec)
        elif name == "anti_examples":
            update[name] = _blocks(sec)
        elif name == "examples":
            ids: dict[str, str] = {}
            for ex in base.examples:
                ids.setdefault(ex.text, ex.chunk_id)
            update[name] = [Example(chunk_id=ids.get(t, OWNER_CHUNK), text=t) for t in _blocks(sec)]
        elif name == "hard_rules":
            update[name] = _hard_rules(sec)
    for name, title in _TITLES.items():
        if name not in known:
            warnings.append(f"Раздела «{title}» нет — оставил как было")
    passport = base.model_copy(update=update)
    return ParseResult(passport, warnings)
