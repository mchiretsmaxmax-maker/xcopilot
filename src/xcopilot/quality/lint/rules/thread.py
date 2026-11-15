"""Тред: 3–8 частей, каждая не длиннее 280 по weighted_len."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class ThreadStructureRule:
    id = "thread_structure"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
