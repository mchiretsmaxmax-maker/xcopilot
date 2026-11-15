"""Чужой материал (ТЗ v3.1): сходство выше 0.6 или общая последовательность от 8 слов — ошибка.

Свои посты сюда не попадают — их проверяет `duplicate_of_recent`.
"""

from __future__ import annotations

from xcopilot.quality.lint.engine import Finding, Level, LintContext, LintDraft
from xcopilot.quality.lint.text import words
from xcopilot.writer.similarity import text_similarity

SIMILARITY_MAX = 0.6
SHARED_WORDS = 8


def shared_run(text: str, other: str, n: int = SHARED_WORDS) -> str | None:
    """Самая первая общая последовательность от n слов (расширенная до максимальной)."""
    mine, theirs = words(text), words(other)
    if len(mine) < n or len(theirs) < n:
        return None
    grams = {tuple(theirs[i : i + n]) for i in range(len(theirs) - n + 1)}
    joined = " " + " ".join(theirs) + " "
    for i in range(len(mine) - n + 1):
        if tuple(mine[i : i + n]) in grams:
            end = i + n
            while end < len(mine) and f" {' '.join(mine[i : end + 1])} " in joined:
                end += 1
            return " ".join(mine[i:end])
    return None


class ForeignSimilarityRule:
    id = "foreign_similarity"
    level: Level = "error"

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]:
        for other in ctx.foreign:
            score = text_similarity(draft.text, other.text)
            if score > SIMILARITY_MAX:
                return [
                    Finding(
                        rule=self.id,
                        level=self.level,
                        message=f"похоже на чужой материал (сходство {score:.2f})",
                        fragment=other.text[:80],
                    )
                ]
            run = shared_run(draft.text, other.text)
            if run is not None:
                return [
                    Finding(
                        rule=self.id,
                        level=self.level,
                        message=f"повторяет чужой материал: {SHARED_WORDS}+ слов подряд",
                        fragment=run,
                    )
                ]
        return []
