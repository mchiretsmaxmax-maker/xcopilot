"""Нарезка длинного текста на куски около max_tokens с перекрытием, по абзацам.

Токен — слово (`\\w+`) или знак препинания: грубая, но детерминированная оценка без
токенизатора модели. Куски — срезы исходного текста по границам токенов: следующий кусок
начинается за `overlap` токенов до конца предыдущего, поэтому текст из кусков
восстанавливается без потерь (см. `chunk_spans`).
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\w+|[^\w\s]")
_PARAGRAPH_GAP = re.compile(r"\n[ \t]*\n")
_SENTENCE_END = frozenset(".!?…")


def count_tokens(text: str) -> int:
    """Число токенов: слова и знаки препинания."""
    return sum(1 for _ in _TOKEN.finditer(text))


def _cut(text: str, tokens: list[tuple[int, int]], start: int, limit: int, floor: int) -> int:
    """Индекс токена, перед которым резать: конец абзаца, иначе конец фразы, иначе limit."""
    for c in range(limit, floor - 1, -1):
        if _PARAGRAPH_GAP.search(text, tokens[c - 1][1], tokens[c][0]):
            return c
    for c in range(limit, floor - 1, -1):
        if text[tokens[c - 1][0] : tokens[c - 1][1]] in _SENTENCE_END:
            return c
    return limit


def chunk_spans(text: str, max_tokens: int = 400, overlap: int = 50) -> list[tuple[int, int]]:
    """Границы кусков (начало, конец) в символах: от 0 до len(text) без дыр, с перекрытием."""
    if max_tokens < 1:
        raise ValueError("max_tokens должен быть >= 1")
    overlap = max(0, min(overlap, max_tokens - 1))
    tokens = [m.span() for m in _TOKEN.finditer(text)]
    n = len(tokens)
    if n == 0:
        return []
    spans: list[tuple[int, int]] = []
    start_tok, start_char = 0, 0
    while True:
        limit = start_tok + max_tokens
        if limit >= n:
            spans.append((start_char, len(text)))
            return spans
        floor = start_tok + max(max_tokens // 2, overlap + 1, 1)
        c = _cut(text, tokens, start_tok, limit, min(floor, limit))
        spans.append((start_char, tokens[c - 1][1]))
        start_tok = c - overlap
        start_char = tokens[start_tok][0] if overlap else tokens[c - 1][1]


def chunk(text: str, max_tokens: int = 400, overlap: int = 50) -> list[str]:
    """Куски текста около max_tokens токенов с перекрытием overlap; пустой текст — []."""
    return [text[s:e] for s, e in chunk_spans(text, max_tokens, overlap)]
