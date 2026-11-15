"""Запрещённые фразы аккаунта и глобальные, без учёта регистра."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft


class BannedPhrasesRule:
    id = "banned_phrases"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        raise NotImplementedError
