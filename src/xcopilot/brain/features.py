"""Признаки материала, посчитанные кодом (ТЗ v3.1, шаг 3 конвейера; этап 7A).

Ключи совпадают с `features.*` в ALLOWED_PATHS (приложение D): по ним строятся детекторы.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

FeatureValue = int | float | bool

_DIGIT = re.compile(r"\d")
_LINK = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_HASHTAG = re.compile(r"(?<![\w#])#\w+")
_FIRST_PERSON = re.compile(
    r"\b(i|i'm|i've|i'd|i'll|me|my|mine|myself|we|our|us|я|мне|меня|мной|мой|моя|моё|мои|мы|нас|наш)\b",
    re.IGNORECASE,
)
_PART_MARK = re.compile(r"^\s*(\d{1,2})\s*/\s*\d{0,2}\b", re.MULTILINE)
# Эмодзи: пиктограммы, символы и флаги; модификаторы и соединители не считаются отдельно.
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\U00002b00-\U00002bff]"
)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.strip().splitlines() if ln.strip()]


def _thread_parts(text: str, kind: str, parts: Sequence[str] | None) -> int:
    if parts:
        return len(parts)
    if kind != "thread":
        return 1
    marks = {int(m.group(1)) for m in _PART_MARK.finditer(text)}
    if marks:
        return max(len(marks), max(marks))
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return max(1, len(blocks))


def compute_features(
    text: str,
    kind: str,
    *,
    parts: Sequence[str] | None = None,
    has_media: bool = False,
) -> dict[str, FeatureValue]:
    """Признаки текста: длина, строки, первая и последняя строки, регистр, эмодзи, ссылки."""
    lines = _lines(text)
    first = lines[0] if lines else ""
    last = lines[-1] if lines else ""
    no_links = _LINK.sub(" ", text)
    letters = [c for c in no_links if c.isalpha()]
    lower = sum(c.islower() for c in letters)
    return {
        "char_len": len(text.strip()),
        "line_count": len(lines),
        "first_line_len": len(first),
        "has_number_first_line": bool(_DIGIT.search(first)),
        "has_question": "?" in no_links,
        "has_question_last_line": last.rstrip().endswith("?"),
        "first_person": bool(_FIRST_PERSON.search(no_links)),
        "emoji_count": len(_EMOJI.findall(text)),
        "hashtag_count": len(_HASHTAG.findall(no_links)),
        "has_link": bool(_LINK.search(text)),
        "has_media": has_media,
        "lowercase_ratio": round(lower / len(letters), 4) if letters else 0.0,
        "thread_parts": _thread_parts(text, kind, parts),
    }
