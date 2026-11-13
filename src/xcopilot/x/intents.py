"""Ссылки публикации Web Intent и подсчёт длины по правилам X.

Если текст длиннее 280 взвешенных знаков, intent не строится (None) — бот
переходит в режим копирования.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

INTENT_URL = "https://x.com/intent/tweet"
MAX_WEIGHTED_LEN = 280
URL_WEIGHT = 23

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_URL_TRAILING_PUNCT = ".,!?:;)»\"'"


def _is_emoji_codepoint(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2B00 <= cp <= 0x2BFF
        or 0x1F1E6 <= cp <= 0x1F1FF
    )


def _is_emoji_continuation(ch: str) -> bool:
    cp = ord(ch)
    return (
        cp == 0xFE0F
        or cp == 0xFE0E
        or 0x1F3FB <= cp <= 0x1F3FF
        or cp == 0x20E3
        or 0xE0020 <= cp <= 0xE007F
    )


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    if (
        0x0000 <= cp <= 0x10FF
        or 0x2000 <= cp <= 0x200D
        or 0x2010 <= cp <= 0x201F
        or 0x2032 <= cp <= 0x2037
    ):
        return 1
    return 2


def _weigh_text_segment(segment: str) -> int:
    total = 0
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if _is_emoji_codepoint(ch):
            i += 1
            while i < n:
                current = segment[i]
                if current == "\u200d" and i + 1 < n and _is_emoji_codepoint(segment[i + 1]):
                    i += 2
                elif _is_emoji_continuation(current):
                    i += 1
                else:
                    break
            total += 2
        else:
            total += _char_weight(ch)
            i += 1
    return total


def weighted_len(text: str) -> int:
    """Длина по правилам X: ссылка = 23, широкие символы (CJK, эмодзи) = 2, прочие = 1."""
    text = unicodedata.normalize("NFC", text)
    total = 0
    last_idx = 0
    for match in _URL_RE.finditer(text):
        total += _weigh_text_segment(text[last_idx : match.start()])
        url = match.group()
        actual_url = url.rstrip(_URL_TRAILING_PUNCT)
        total += URL_WEIGHT
        last_idx = match.start() + len(actual_url)
    total += _weigh_text_segment(text[last_idx:])
    return total


def _encode(params: list[tuple[str, str]]) -> str:
    return INTENT_URL + "?" + "&".join(f"{k}={quote(v, safe='')}" for k, v in params)


def build_post_intent(text: str) -> str | None:
    """Ссылка «опубликовать пост» или None, если длиннее 280."""
    if weighted_len(text) > MAX_WEIGHTED_LEN:
        return None
    return _encode([("text", text)])


def build_reply_intent(text: str, in_reply_to_id: str) -> str | None:
    """Ссылка «ответить на пост» (параметр in_reply_to) или None, если длиннее 280."""
    if weighted_len(text) > MAX_WEIGHTED_LEN:
        return None
    return _encode([("text", text), ("in_reply_to", in_reply_to_id)])


def build_quote_intent(text: str, quoted_url: str) -> str | None:
    """Ссылка «цитата»: адрес цитируемого поста в конце текста; None, если длиннее 280."""
    full = f"{text} {quoted_url}"
    if weighted_len(full) > 280:
        return None
    return _encode([("text", full)])
