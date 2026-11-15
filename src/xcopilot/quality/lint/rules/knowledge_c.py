"""Знания класса C (ТЗ v3.1) — мягкие запреты: фраза из знания в тексте → предупреждение."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import find_ci


class ClassCRule:
    id = "knowledge_c"
    level: Level = "warn"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        out: list[Finding] = []
        for rule in ctx.class_c:
            for phrase in rule.phrases:
                found = find_ci(draft.text, phrase)
                if found is not None:
                    out.append(
                        Finding(
                            rule=self.id, level=self.level, message=rule.statement, fragment=found
                        )
                    )
                    break
        return out
