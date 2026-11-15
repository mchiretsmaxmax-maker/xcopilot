"""Хэштеги: none — ни одного, rare — один; автоисправление."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class HashtagsRule:
    id = "hashtags"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
