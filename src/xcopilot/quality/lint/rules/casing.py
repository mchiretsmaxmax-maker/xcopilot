"""Регистр: при casing=lower — только строчные; автоисправление.

Ссылки, @упоминания, #хэштеги и $тикеры не трогаются.
"""

from __future__ import annotations

import re

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import protected_spans


def _lower_outside(text: str) -> str:
    out, last = [], 0
    for start, end in protected_spans(text):
        if start < last:
            continue
        out.append(text[last:start].lower())
        out.append(text[start:end])
        last = end
    out.append(text[last:].lower())
    return "".join(out)


class CasingRule:
    id = "casing"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        if ctx.hard_rules.casing != "lower":
            return []
        lowered = _lower_outside(draft.text)
        if lowered == draft.text and all(_lower_outside(p) == p for p in draft.parts):
            return []
        first = next((w for w in re.findall(r"\w+", draft.text) if w != w.lower()), draft.text[:20])
        return [Finding(rule=self.id, level=self.level, message="заглавные буквы", fragment=first)]

    def fix(self, draft: LintDraft, ctx: LintContext) -> LintDraft | None:
        if ctx.hard_rules.casing != "lower":
            return None
        return LintDraft(
            text=_lower_outside(draft.text),
            format=draft.format,
            parts=tuple(_lower_outside(p) for p in draft.parts),
        )
