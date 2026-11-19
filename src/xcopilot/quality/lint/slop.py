"""Детектор AI-шаблонов (no-ai-slop): запрещённые слова, тире, регэкспы шаблонов.

Правила — по скилу no-ai-slop (MIT License, Copyright (c) 2026 Peter Yang,
https://github.com/petergyang/no-ai-slop), адаптированы под короткие тексты X. Все списки и
регэкспы — в `config/ai_slop.toml` по языкам. Язык черновика неизвестен — угадывается по
доле кириллицы. Детектор используется правилом линтера `ai_slop` и eval-набором.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from xcopilot.errors import ConfigError

SLOP = Path(__file__).resolve().parents[4] / "config" / "ai_slop.toml"
_DASH = re.compile(r"—|(?<=\w)\s*–\s*(?=\w)")
# En dash в диапазоне — типографика: «11–12%», «$5–10», «November–December», «mon–fri».
_NUM_RANGE = re.compile(r"[\d%]\s*–\s*\$?\d")
_MONTH_DAY = (
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|mon|tue|tues|wed|thu|thur|thurs"
    r"|fri|sat|sun)[a-z]*"
)
_WORD_RANGE = re.compile(rf"\b{_MONTH_DAY}\s*–\s*{_MONTH_DAY}\b", re.I)


def _dash(text: str) -> re.Match[str] | None:
    """Первое тире в тексте; en dash в диапазоне цифр, месяцев или дней недели — не тире."""
    for m in _DASH.finditer(text):
        if m.group().strip() == "–":
            around = text[max(0, m.start() - 12) : m.end() + 12]
            at = m.start() - max(0, m.start() - 12)
            if any(r.start() <= at < r.end() for rx in (_NUM_RANGE, _WORD_RANGE)
                   for r in rx.finditer(around)):  # fmt: skip
                continue
        return m
    return None


_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)


@dataclass(frozen=True)
class SlopHit:
    """Найденный шаблон: код, цитата из текста и что сказать писателю."""

    code: str
    fragment: str
    message: str


@dataclass(frozen=True)
class SlopPattern:
    code: str
    message: str
    rx: re.Pattern[str]
    formats: frozenset[str] = frozenset()  # пусто — для всех форматов


@dataclass(frozen=True)
class LangRules:
    em_dash: bool = False
    words: tuple[str, ...] = ()
    patterns: tuple[SlopPattern, ...] = ()
    words_rx: re.Pattern[str] | None = None


@dataclass(frozen=True)
class SlopConfig:
    langs: Mapping[str, LangRules] = field(default_factory=dict)

    def lang(self, lang: str) -> LangRules:
        return self.langs.get(lang, LangRules())


def _lang_rules(name: str, raw: Mapping[str, Any]) -> LangRules:
    words = tuple(str(w).strip() for w in raw.get("words", []) if str(w).strip())
    words_rx = None
    if words:
        alt = "|".join(re.escape(w.replace("’", "'")) for w in sorted(words, key=len, reverse=True))
        words_rx = re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])", re.I)
    patterns = []
    for item in raw.get("patterns", []):
        try:
            rx = re.compile(str(item["regex"]), re.I)
        except (KeyError, re.error) as exc:
            raise ConfigError(f"ai_slop.toml [{name}]: плохой шаблон {item!r}: {exc}") from exc
        formats = frozenset(str(f) for f in item.get("formats", []))
        patterns.append(
            SlopPattern(
                code=str(item["code"]), message=str(item.get("message", "")), rx=rx, formats=formats
            )
        )
    return LangRules(
        em_dash=bool(raw.get("em_dash", False)),
        words=words,
        patterns=tuple(patterns),
        words_rx=words_rx,
    )


@cache
def load_slop_config(path: Path = SLOP) -> SlopConfig:
    """Правила из `config/ai_slop.toml`; нет файла — пустые правила (детектор молчит)."""
    if not path.exists():
        return SlopConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return SlopConfig(
        langs={k: _lang_rules(k, v) for k, v in data.items() if isinstance(v, Mapping)}
    )


def guess_lang(text: str) -> str:
    """ru, если кириллицы больше латиницы, иначе en."""
    return "ru" if len(_CYR.findall(text)) > len(_LAT.findall(text)) else "en"


def detect_slop(
    text: str, lang: str | None, config: SlopConfig | None = None, fmt: str | None = None
) -> list[SlopHit]:
    """Все шаблоны в тексте: по одной находке на код шаблона (первое место), порядок — конфиг.

    Шаблон с `formats` (например advice — только reply/quote) проверяется лишь для этих
    форматов; формат не указан — такие шаблоны пропускаются."""
    rules = (config or load_slop_config()).lang(lang or guess_lang(text))
    norm = text.replace("’", "'")  # длина та же: цитаты берутся из исходного текста по позициям
    hits: list[SlopHit] = []
    seen: set[str] = set()

    def add(code: str, start: int, end: int, message: str) -> None:
        if code not in seen:
            seen.add(code)
            hits.append(SlopHit(code, text[start:end].strip(), message))

    if rules.em_dash and (m := _dash(norm)):
        a, b = max(0, m.start() - 20), min(len(text), m.end() + 20)
        add("em_dash", a, b, "тире в коротком тексте — замени точкой или запятой")
    if rules.words_rx is not None and (m := rules.words_rx.search(norm)):
        add("banned_word", m.start(), m.end(), "запрещённое AI-слово — скажи проще")
    for p in rules.patterns:
        if p.formats and fmt not in p.formats:
            continue
        if m := p.rx.search(norm):
            add(p.code, m.start(), m.end(), p.message)
    return hits
