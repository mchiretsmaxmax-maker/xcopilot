"""Регистр: при casing=lower — только строчные; автоисправление."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class CasingRule:
    id = "casing"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
