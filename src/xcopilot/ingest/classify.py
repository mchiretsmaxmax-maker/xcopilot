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


def classify(message: Incoming) -> InputKind:
    if message.file_name is not None:
        return InputKind.FILE

    text = (message.text or "").strip()
    if not text:
        return InputKind.EMPTY

    tokens = text.split()
    if all(parse_post_url(token) is not None and _POST_URL.fullmatch(token) for token in tokens):
        return InputKind.LINKS

    if len(tokens) == 1:
        token = tokens[0]
        if (token.startswith("@") or _HANDLE_URL.fullmatch(token)) and parse_handle(
            token
        ) is not None:
            return InputKind.HANDLE

    return InputKind.TEXT
