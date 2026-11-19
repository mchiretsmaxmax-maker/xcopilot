"""Полоса длины варианта ответа/цитаты (заглушка)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class LengthBandRule:
    id = "length_band"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        return []
