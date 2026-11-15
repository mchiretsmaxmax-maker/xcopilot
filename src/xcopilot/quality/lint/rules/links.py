"""Ссылки: none — ни одной, rare — одна."""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import URL_RE


class LinksRule:
    id = "links"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        allowed = {"none": 0, "rare": 1}.get(ctx.hard_rules.links)
        if allowed is None:
            return []
        urls = URL_RE.findall(draft.text)
        if len(urls) <= allowed:
            return []
        return [
            Finding(
                rule=self.id,
                level=self.level,
                message=f"ссылки: {len(urls)}, можно {allowed}",
                fragment=urls[allowed],
            )
        ]
