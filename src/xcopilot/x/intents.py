"""Ссылки публикации Web Intent и подсчёт длины по правилам X.

Если текст длиннее 280 взвешенных знаков, intent не строится (None) — бот
переходит в режим копирования.
"""

from __future__ import annotations

INTENT_URL = "https://x.com/intent/tweet"
MAX_WEIGHTED_LEN = 280
URL_WEIGHT = 23


def weighted_len(text: str) -> int:
    """Длина по правилам X: ссылка = 23, широкие символы (CJK, эмодзи) = 2, прочие = 1."""
    raise NotImplementedError


def build_post_intent(text: str) -> str | None:
    """Ссылка «опубликовать пост» или None, если длиннее 280."""
    raise NotImplementedError


def build_reply_intent(text: str, in_reply_to_id: str) -> str | None:
    """Ссылка «ответить на пост» (параметр in_reply_to) или None, если длиннее 280."""
    raise NotImplementedError


def build_quote_intent(text: str, quoted_url: str) -> str | None:
    """Ссылка «цитата»: адрес цитируемого поста в конце текста; None, если длиннее 280."""
    raise NotImplementedError
