"""Хэштеги: none — ни одного, rare — один; лишние убираются автоисправлением."""

from __future__ import annotations

import re

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import HASHTAG_RE


def _allowed(ctx: LintContext) -> int | None:
    return {"none": 0, "rare": 1}.get(ctx.hard_rules.hashtags)


def _drop_extra(text: str, keep: int) -> str:
    matches = list(HASHTAG_RE.finditer(text))
    out, last = [], 0
    for m in matches[keep:]:
        out.append(text[last : m.start()].rstrip(" \t"))
        last = m.end()
    out.append(text[last:])
    joined = "".join(out)
    joined = re.sub(r"[ \t]{2,}", " ", joined)
    return "\n".join(line.strip() for line in joined.splitlines()).strip()


class HashtagsRule:
    id = "hashtags"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        allowed = _allowed(ctx)
        if allowed is None:
            return []
        tags = HASHTAG_RE.findall(draft.text)
        if len(tags) <= allowed:
            return []
        return [
            Finding(
                rule=self.id,
                level=self.level,
                message=f"хэштеги: {len(tags)}, можно {allowed}",
                fragment=" ".join(tags[allowed:]),
            )
        ]

    def fix(self, draft: LintDraft, ctx: LintContext) -> LintDraft | None:
        allowed = _allowed(ctx)
        if allowed is None:
            return None
        text = _drop_extra(draft.text, allowed)
        if not text:
            return None  # пост из одних хэштегов не «исправляем» в пустоту
        parts = tuple(_drop_extra(p, allowed) for p in draft.parts)
        return LintDraft(text=text, format=draft.format, parts=parts)
