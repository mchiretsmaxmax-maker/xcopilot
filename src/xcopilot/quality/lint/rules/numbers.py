"""Каждая цифра — из фактов пакета или ввода пользователя (политика цифр)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.numbers import extract_numbers, is_grounded


class NumberGroundingRule:
    id = "number_grounding"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        return [
            Finding(
                rule=self.id,
                level=self.level,
                message="цифра без источника в фактах",
                fragment=tok.raw,
            )
            for tok in extract_numbers(draft.text)
            if not tok.skip and not is_grounded(tok, ctx.facts, ctx.user_input)
        ]
