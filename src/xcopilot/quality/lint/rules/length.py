"""Длина по правилам X (weighted_len): ответ и цитата — до 280.

Пост потолка не имеет (16.11.2026): длинный режется кодом на части-тред, `hard_rules.max_len`
паспорта его не ограничивает.
"""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.x.intents import weighted_len

X_LIMIT = 280


class LengthRule:
    id = "length"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        if draft.format not in ("reply", "quote"):
            return []  # пост — без потолка, части треда проверяет thread_structure
        limit = min(ctx.hard_rules.max_len, X_LIMIT)
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
