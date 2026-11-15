"""Запрещённые фразы аккаунта (паспорт) и глобальные, без учёта регистра и ё."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import find_ci, fold


class BannedPhrasesRule:
    id = "banned_phrases"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        seen: set[str] = set()
        out: list[Finding] = []
        for phrase in (*ctx.hard_rules.banned_phrases, *ctx.global_banned):
            key = fold(phrase).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            found = find_ci(draft.text, phrase)
            if found is not None:
                out.append(
                    Finding(
                        rule=self.id,
                        level=self.level,
                        message="запрещённая фраза",
                        fragment=found,
                    )
                )
        return out
