"""Разбор ссылок на посты и хэндлов X (x.com, twitter.com, mobile., /i/web/status/, параметры)."""

from __future__ import annotations

import re

from xcopilot.x.models import PostRef

_POST_RE = re.compile(
    r"(?<![\w./])"
    r"(?:https?://)?(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/"
    r"(?:(?P<handle>[A-Za-z0-9_]{1,15})|i(?:/web)?)"
    r"/(?:status|statuses)/(?P<id>\d{1,20})(?![0-9A-Za-z])",
    re.IGNORECASE,
)

_RESERVED_HANDLES = {
    "i",
    "home",
    "explore",
    "search",
    "settings",
    "messages",
    "notifications",
    "intent",
    "share",
    "hashtag",
    "login",
    "signup",
    "tos",
    "privacy",
}

_PROFILE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/",
    re.IGNORECASE,
)


def parse_post_url(text: str) -> PostRef | None:
    """Первая ссылка на пост в тексте или None."""
    match = _POST_RE.search(text)
    if match is None:
        return None
    handle = match.group("handle")
    if handle is not None and handle.lower() == "i":
        handle = None
    return PostRef(match.group("id"), handle)


def parse_handle(text: str) -> str | None:
    """Хэндл без @ из «@name», «name» или ссылки на профиль; None, если не хэндл."""
    s = text.strip()
    if not s:
        return None

    profile_match = _PROFILE_URL_RE.match(s)
    if profile_match is not None:
        rest = s[profile_match.end() :]
        segment = re.split(r"[/?#]", rest, maxsplit=1)[0]
        if not segment:
            return None
        candidate = segment
    else:
        if "://" in s:
            return None
        first_token = re.split(r"[/?#]", s, maxsplit=1)[0]
        if "." in first_token:
            return None
        candidate = s[1:] if s.startswith("@") else s

    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", candidate):
        return None
    if candidate.lower() in _RESERVED_HANDLES:
        return None
    return candidate


def extract_post_urls(text: str) -> list[PostRef]:
    """Все ссылки на посты в тексте по порядку, без повторов id."""
    result: list[PostRef] = []
    seen: set[str] = set()
    for match in _POST_RE.finditer(text):
        post_id = match.group("id")
        if post_id in seen:
            continue
        seen.add(post_id)
        handle = match.group("handle")
        if handle is not None and handle.lower() == "i":
            handle = None
        result.append(PostRef(post_id, handle))
    return result
