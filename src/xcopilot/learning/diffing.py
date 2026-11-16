"""Разбор правки владельца: сокращение, удалённая фраза, замена слова, регистр, перестройка.

Работает по словам без учёта регистра и знаков препинания (difflib), без моделей: результат —
сводка, по которой код проверяет предложения писателя и считает повторы.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

_WORD = re.compile(r"[\w'’]+")
SHORTENING_RATIO = 0.85  # после правки текст короче 85% исходного
RESTRUCTURE_SIMILARITY = 0.75  # порядок слов совпадает меньше, чем на 75%
RESTRUCTURE_OVERLAP = 0.8  # но набор слов почти тот же
MAX_REPLACED_WORDS = 3  # замена слова или оборота; длиннее — переписанный кусок


@dataclass(frozen=True)
class EditSummary:
    kinds: frozenset[str] = (
        frozenset()
    )  # shortening, removed_phrase, word_replace, casing, restructure
    removed: tuple[str, ...] = ()  # удалённые фразы (строчными), в порядке текста
    added: tuple[str, ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()
    removed_at_start: bool = False
    length_ratio: float = 1.0
    casing: str | None = None  # lower / upper / mixed — регистр изменился


def _words(text: str) -> list[str]:
    return [w.strip("'’").lower() for w in _WORD.findall(text) if w.strip("'’")]


def _casing(before: str, after: str) -> str | None:
    if before == after:
        return None
    if after == after.lower() and before != before.lower():
        return "lower"
    if after == after.upper() and before != before.upper() and any(c.isalpha() for c in after):
        return "upper"
    if before.lower() == after.lower():
        return "mixed"
    return None


def classify_edit(before: str, after: str) -> EditSummary:
    """Что владелец сделал с текстом: сократил, убрал фразу, заменил слово, сменил регистр."""
    before, after = before.strip(), after.strip()
    kinds: set[str] = set()
    removed: list[str] = []
    added: list[str] = []
    replaced: list[tuple[str, str]] = []
    at_start = False
    old, new = _words(before), _words(after)
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            removed.append(" ".join(old[i1:i2]))
            at_start = at_start or i1 == 0
        elif tag == "insert":
            added.append(" ".join(new[j1:j2]))
        elif tag == "replace":
            if i2 - i1 <= MAX_REPLACED_WORDS and j2 - j1 <= MAX_REPLACED_WORDS:
                replaced.append((" ".join(old[i1:i2]), " ".join(new[j1:j2])))
            else:
                added.append(" ".join(new[j1:j2]))
    if removed:
        kinds.add("removed_phrase")
    if replaced:
        kinds.add("word_replace")
    ratio = round(len(after) / len(before), 4) if before else 1.0
    if before and ratio <= SHORTENING_RATIO:
        kinds.add("shortening")
    casing = _casing(before, after)
    if casing is not None:
        kinds.add("casing")
    if old and new and matcher.ratio() < RESTRUCTURE_SIMILARITY:
        overlap = sum((Counter(old) & Counter(new)).values()) / len(old)
        if overlap >= RESTRUCTURE_OVERLAP:
            kinds.add("restructure")
    return EditSummary(
        kinds=frozenset(kinds),
        removed=tuple(removed),
        added=tuple(added),
        replacements=tuple(replaced),
        removed_at_start=at_start,
        length_ratio=ratio,
        casing=casing,
    )
