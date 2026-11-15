"""Длина по правилам X (weighted_len): пост — до потолка паспорта, ответ и цитата — до 280."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.x.intents import weighted_len

X_LIMIT = 280


class LengthRule:
    id = "length"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        if draft.format == "thread":
            return []  # части треда проверяет thread_structure
        cap = ctx.hard_rules.max_len
        limit = cap if draft.format == "post" else min(cap, X_LIMIT)
        size = weighted_len(draft.text)
        if size <= limit:
            return []
        return [
            Finding(
                rule=self.id,
                level=self.level,
                message=f"длина {size} больше {limit}",
                fragment=draft.text[limit:][:60],
            )
        ]
