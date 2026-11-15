"""Каждая цифра — из фактов или ввода пользователя."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class NumberGroundingRule:
    id = "number_grounding"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
