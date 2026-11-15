"""Сходство с чужим материалом выше 0.6 или общие 8 слов подряд (v3.1)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class ForeignSimilarityRule:
    id = "foreign_similarity"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
