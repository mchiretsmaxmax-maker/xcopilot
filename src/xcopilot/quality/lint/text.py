"""Общие разборщики текста для правил: ссылки, упоминания, хэштеги, тикеры, эмодзи, слова."""

from __future__ import annotations

import re

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
MENTION_RE = re.compile(r"(?<![\w@])@\w{1,15}")
HASHTAG_RE = re.compile(r"(?<![\w#&])#\w+")
TICKER_RE = re.compile(r"(?<![\w$])\$[A-Za-z][A-Za-z0-9]{0,9}\b")
WORD_RE = re.compile(r"\w+")
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1FC00, 0x1FFFF),
)
_EMOJI_JOINERS = {0x200D, 0xFE0F, 0xFE0E, 0x20E3} | set(range(0x1F3FB, 0x1F400))


def _is_emoji(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def emoji_clusters(text: str) -> list[str]:
    """Эмодзи текста; составной (ZWJ, модификаторы, флаги) — одна штука."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        cp = ord(text[i])
        if not _is_emoji(cp):
            i += 1
            continue
        start = i
        i += 1
        while i < n:
            nxt = ord(text[i])
            if nxt in _EMOJI_JOINERS:
                i += 1
                if nxt == 0x200D and i < n and _is_emoji(ord(text[i])):
                    i += 1
            elif 0x1F1E6 <= cp <= 0x1F1FF and 0x1F1E6 <= nxt <= 0x1F1FF and i - start == 1:
                i += 1
            else:
                break
        out.append(text[start:i])
    return out


def protected_spans(text: str) -> list[tuple[int, int]]:
    """Куски, которые правила регистра и языка не трогают: ссылки, @, #, $тикеры."""
    spans = [
        m.span() for rx in (URL_RE, MENTION_RE, HASHTAG_RE, TICKER_RE) for m in rx.finditer(text)
    ]
    return sorted(spans)


def strip_protected(text: str) -> str:
    out, last = [], 0
    for start, end in protected_spans(text):
        if start < last:
            continue
        out.append(text[last:start])
        out.append(" ")
        last = end
    out.append(text[last:])
    return "".join(out)


def fold(text: str) -> str:
    """Для сравнения: нижний регистр, ё → е, типографский апостроф → '."""
    return text.lower().replace("ё", "е").replace("’", "'")


def words(text: str) -> list[str]:
    return WORD_RE.findall(fold(text))


def find_ci(text: str, phrase: str) -> str | None:
    """Найти фразу без учёта регистра и ё; вернуть найденный кусок исходного текста."""
    needle = fold(phrase).strip()
    if not needle:
        return None
    pos = fold(text).find(needle)
    return text[pos : pos + len(needle)] if pos >= 0 else None
