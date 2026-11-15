"""Язык текста совпадает с языком задачи (по доле кириллицы и латиницы)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import strip_protected

MIN_LETTERS = 20
MAX_FOREIGN = {"ru": 0.5, "en": 0.2}


class LanguageRule:
    id = "language"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        if ctx.lang not in MAX_FOREIGN:
            return []
        text = strip_protected(draft.text)
        cyr = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch in "ёЁ")
        lat = sum(1 for ch in text if "a" <= ch.lower() <= "z")
        total = cyr + lat
        if total < MIN_LETTERS:
            return []
        foreign = (lat if ctx.lang == "ru" else cyr) / total
        if foreign <= MAX_FOREIGN[ctx.lang]:
            return []
        return [
            Finding(
                rule=self.id,
                level=self.level,
                message=f"язык текста не {ctx.lang}",
                fragment=draft.text[:40],
            )
        ]
