"""Сходство со своими постами за 60 дней выше 0.85 — повтор."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.writer.similarity import text_similarity

DUPLICATE_MIN = 0.85


class DuplicateOfRecentRule:
    id = "duplicate_of_recent"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        for own in ctx.own_recent:
            score = text_similarity(draft.text, own)
            if score >= DUPLICATE_MIN:
                return [
                    Finding(
                        rule=self.id,
                        level=self.level,
                        message=f"повтор своего поста (сходство {score:.2f})",
                        fragment=own[:80],
                    )
                ]
        return []
