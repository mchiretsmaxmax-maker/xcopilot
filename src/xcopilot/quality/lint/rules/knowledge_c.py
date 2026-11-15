"""Знания класса C — предупреждения линтера (v3.1)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class ClassCRule:
    id = "knowledge_c"
    level: Level = "warn"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
