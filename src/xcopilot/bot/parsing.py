"""Разбор пользовательского ввода: хэндлы X, примеры стиля, деньги, часовые пояса."""

from __future__ import annotations

import re
from decimal import Decimal
from zoneinfo import ZoneInfo

_NAME = r"[A-Za-z0-9_]{1,15}"
_HOST = r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com"
_PROFILE_URL = re.compile(rf"^{_HOST}/@?({_NAME})/?(?:[?#].*)?$", re.IGNORECASE)
_POST_URL = re.compile(rf"^{_HOST}/{_NAME}/status/\d+(?:[/?#].*)?$", re.IGNORECASE)
_PLAIN = re.compile(_NAME)
_TZ_NAME = re.compile(r"^[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*$")
_USD = re.compile(r"^\d+(?:\.\d{1,6})?$")
_MAX_USD = 1000


def normalize_handle(raw: str) -> str | None:
    """`@Name`, `x.com/name`, `https://twitter.com/name/` → `name`; не аккаунт → None."""
    value = raw.strip()
    if not value or any(ch.isspace() for ch in value):
        return None
    match = _PROFILE_URL.match(value)
    if match:
        return match.group(1).lower()
    value = value.removeprefix("@")
    return value.lower() if _PLAIN.fullmatch(value) else None


def parse_handles(text: str) -> tuple[list[str], list[str]]:
    """Разбить текст на токены; вернуть (нормализованные хэндлы без повторов, мусор)."""
    valid: list[str] = []
    garbage: list[str] = []
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        handle = normalize_handle(token)
        if handle is None:
            garbage.append(token)
        elif handle not in valid:
            valid.append(handle)
    return valid, garbage


def classify_sample(text: str) -> tuple[str, str]:
    """Пример стиля → (вид, значение): `handle` (@имя или ссылка на профиль),
    `url` (ссылка на пост), `text` (остальное)."""
    value = text.strip()
    single = bool(value) and not any(ch.isspace() for ch in value)
    if single and _POST_URL.match(value):
        return "url", value
    if single and (value.startswith("@") or _PROFILE_URL.match(value)):
        handle = normalize_handle(value)
        if handle is not None:
            return "handle", handle
    return "text", value


def parse_usd_to_micro(text: str) -> int | None:
    """«7.5» или «$7,5» → микродоллары; ≤0, не число или >1000$ → None."""
    value = text.strip().removeprefix("$").replace(",", ".")
    if not _USD.fullmatch(value):
        return None
    micro = int(Decimal(value) * 1_000_000)
    if micro <= 0 or micro > _MAX_USD * 1_000_000:
        return None
    return micro


def parse_timezone(text: str) -> str | None:
    """Название пояса IANA → оно же; неизвестное → None."""
    name = text.strip()
    if not _TZ_NAME.fullmatch(name):
        return None
    try:
        ZoneInfo(name)
    except (ValueError, OSError, KeyError):
        return None
    return name
