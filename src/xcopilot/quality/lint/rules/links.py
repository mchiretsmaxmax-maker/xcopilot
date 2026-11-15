"""Ссылки: none — ни одной, rare — одна."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class LinksRule:
    id = "links"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
