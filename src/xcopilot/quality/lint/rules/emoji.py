"""Эмодзи: none — ни одного, rare — не больше одного."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import emoji_clusters


class EmojiRule:
    id = "emoji"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        allowed = {"none": 0, "rare": 1}.get(ctx.hard_rules.emoji)
        if allowed is None:
            return []
        found = emoji_clusters(draft.text)
        if len(found) <= allowed:
            return []
        return [
            Finding(
                rule=self.id,
                level=self.level,
                message=f"эмодзи: {len(found)}, можно {allowed}",
                fragment="".join(found[allowed:][:5]),
            )
        ]
