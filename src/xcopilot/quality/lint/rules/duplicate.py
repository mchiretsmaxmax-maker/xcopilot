"""Сходство со своими постами за 60 дней выше 0.85."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class DuplicateOfRecentRule:
    id = "duplicate_of_recent"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
