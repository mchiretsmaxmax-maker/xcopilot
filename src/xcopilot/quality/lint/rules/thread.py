"""Тред: ≥ 1 часть, каждая не длиннее 280 по weighted_len (с 16.11.2026 «3–8» снято)."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.x.intents import weighted_len

PART_LIMIT = 280


class ThreadStructureRule:
    id = "thread_structure"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        if draft.format != "thread":
            return []
        parts = [p for p in draft.parts if p.strip()]
        out: list[Finding] = []
        if not parts:
            out.append(Finding(rule=self.id, level=self.level, message="в треде нет частей"))
        for no, part in enumerate(parts, start=1):
            size = weighted_len(part)
            if size > PART_LIMIT:
                out.append(
                    Finding(
                        rule=self.id,
                        level=self.level,
                        message=f"часть {no} длиннее {PART_LIMIT} ({size})",
                        fragment=part[:60],
                    )
                )
        return out
