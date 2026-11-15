"""Шаблоны AI-текста RU и EN.

Ошибки: «не X, а Y», избыток тире, штампы-зачины. Предупреждение: тройные перечисления.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import fold

DASH_LIMIT = 3


@dataclass(frozen=True)
class _Pattern:
    code: str
    rx: re.Pattern[str]
    message: str
    level: Level = "error"


_I = re.IGNORECASE
PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "not_x_but_y",
        re.compile(r"\bне\s+(?:просто\s+|только\s+)?[^,.!?;:\n—–]{1,40}?,\s*а\s", _I),
        "противопоставление «не X, а Y»",
    ),
    _Pattern(
        "not_only_but",
        re.compile(r"\bне только\b[^.!?\n]{1,60}?,?\s*но и\b", _I),
        "конструкция «не только …, но и …»",
    ),
    _Pattern(
        "not_x_but_y",
        re.compile(
            r"\b(?:it's|this is|that's|is)\s+not\s+(?:just\s+|only\s+)?[^,.;!?\n—]{1,40}?"
            r"(?:,|;|—|\s-)\s*(?:it's|but|this is|it is)\b",
            _I,
        ),
        "противопоставление «not X, but Y»",
    ),
    _Pattern(
        "not_only_but",
        re.compile(r"\bnot\s+(?:just|only)\s+[^.!?\n]{1,40}?,?\s+but\s+(?:also\s+)?", _I),
        "конструкция «not only …, but …»",
    ),
    _Pattern(
        "stock_phrase",
        re.compile(
            r"(?:в современном мире|в наше время|в эпоху\s+\w+|давайте разбер[её]мся|"
            r"давайте поговорим|погрузимся в|стоит отметить|важно понимать|подводя итог|"
            r"таким образом|в заключение|in today's|let's dive|delve into|it's worth noting|"
            r"in conclusion|game-changer)",
            _I,
        ),
        "штамп",
    ),
    _Pattern(
        "triple",
        re.compile(r"\b\w+,\s+\w+\s+(?:и|and)\s+\w+\b", _I),
        "тройное перечисление",
        "warn",
    ),
)
_DASH = re.compile(r"—|–|\s-\s")


class AiPatternsRule:
    id = "ai_patterns"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        text = draft.text.replace("’", "'")
        folded = fold(text)  # длина та же: ё → е и апостроф — один символ на один
        out: list[Finding] = []
        for p in PATTERNS:
            m = p.rx.search(folded)
            if m is not None:
                out.append(
                    Finding(
                        rule=self.id,
                        level=p.level,
                        message=p.message,
                        fragment=draft.text[m.start() : m.end()].strip(),
                    )
                )
        dashes = _DASH.findall(text)
        if len(dashes) >= DASH_LIMIT:
            out.append(
                Finding(
                    rule=self.id,
                    level=self.level,
                    message=f"избыток тире: {len(dashes)}",
                    fragment="—" * len(dashes),
                )
            )
        return out
