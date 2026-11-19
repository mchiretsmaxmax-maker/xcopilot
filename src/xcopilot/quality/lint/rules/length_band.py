"""Полоса длины варианта ответа/цитаты: вне своей длины — ошибка (переписать, не отказ)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.writer.lengths import LENGTH_FORMATS, length_issue
from xcopilot.x.intents import weighted_len


class LengthBandRule:
    id = "length_band"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        if draft.format not in LENGTH_FORMATS or draft.length_slot is None:
            return []
        size = weighted_len(draft.text)
        issue = length_issue(draft.length_slot, size, ctx.length_bands)
        if issue is None:
            return []
        verb = "укороти" if issue.kind == "too_long" else "удлини"
        message = f"{verb} до {issue.target} знаков (сейчас {size})"
        return [Finding(rule=self.id, level=self.level, message=message)]
