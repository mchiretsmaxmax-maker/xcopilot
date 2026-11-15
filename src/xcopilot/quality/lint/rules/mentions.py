"""@упоминания — только те, что есть во вводе пользователя или фактах."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import MENTION_RE


class MentionSafetyRule:
    id = "mention_safety"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        allowed = {m.lower() for s in (ctx.user_input, *ctx.facts) for m in MENTION_RE.findall(s)}
        out: list[Finding] = []
        for handle in dict.fromkeys(MENTION_RE.findall(draft.text)):
            if handle.lower() not in allowed:
                out.append(
                    Finding(
                        rule=self.id,
                        level=self.level,
                        message="упоминание не из ввода",
                        fragment=handle,
                    )
                )
        return out
