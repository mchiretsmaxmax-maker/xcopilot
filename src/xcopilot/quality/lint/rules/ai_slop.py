"""Правило `ai_slop`: шаблоны no-ai-slop из `config/ai_slop.toml` (см. `quality.lint.slop`).

Каждый найденный шаблон — ошибка: черновик уходит на переписывание с цитатой места.
"""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.slop import detect_slop


class AiSlopRule:
    id = "ai_slop"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        return [
            Finding(rule=self.id, level=self.level, message=h.message, fragment=h.fragment)
            for h in detect_slop(draft.text, ctx.lang)
        ]
