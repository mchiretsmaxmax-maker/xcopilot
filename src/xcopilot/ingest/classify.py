"""Определение типа входа: текст, ссылки на посты, хэндл, файл."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from xcopilot.x.urls import parse_handle, parse_post_url


class InputKind(StrEnum):
    TEXT = "text"
    LINKS = "links"
    HANDLE = "handle"
    FILE = "file"
    EMPTY = "empty"
    ARTICLE = "article"  # ТЗ v3.1: ссылки на страницы вне X
    SCREENSHOT = "screenshot"  # ТЗ v3.1: картинки (определяет сервис: в Incoming их нет)
    BATCH = "batch"  # ТЗ v3.1: пачка из нескольких входов


@dataclass(frozen=True, slots=True)
class Incoming:
    """Что прислал пользователь: текст и/или файл (имя и заявленный размер)."""

    text: str | None = None
    file_name: str | None = None
    file_size: int | None = None


_POST_URL = re.compile(
    r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/\w{1,15}/status(?:es)?/\d+(?:[/?#]\S*)?",
    re.I,
)
_HANDLE_URL = re.compile(
    r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/@?\w{1,15}/?",
    re.I,
)


_WEB_URL = re.compile(r"https?://[^\s/?#]+\.[^\s/?#]+\S*", re.I)
_X_HOST = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com(?:[/?#:]|$)", re.I)


def _is_article_url(token: str) -> bool:
    return bool(_WEB_URL.fullmatch(token)) and not _X_HOST.match(token)


def _is_post_url(token: str) -> bool:
    return parse_post_url(token) is not None and bool(_POST_URL.fullmatch(token))


def article_urls(text: str) -> list[str]:
    """Ссылки на страницы вне X из сообщения, состоящего только из ссылок (без повторов)."""
    return list(dict.fromkeys(t for t in text.split() if _is_article_url(t)))


def classify(message: Incoming) -> InputKind:
    if message.file_name is not None:
        return InputKind.FILE

    text = (message.text or "").strip()
    if not text:
        return InputKind.EMPTY

    tokens = text.split()
    if all(_is_post_url(token) for token in tokens):
        return InputKind.LINKS
    if all(_is_post_url(t) or _is_article_url(t) for t in tokens):
        return InputKind.LINKS if any(_is_post_url(t) for t in tokens) else InputKind.ARTICLE

    if len(tokens) == 1:
        token = tokens[0]
        if (token.startswith("@") or _HANDLE_URL.fullmatch(token)) and parse_handle(
            token
        ) is not None:
            return InputKind.HANDLE

    return InputKind.TEXT
