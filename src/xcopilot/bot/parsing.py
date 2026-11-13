"""Разбор пользовательского ввода: хэндлы X, примеры стиля, деньги, часовые пояса."""

from __future__ import annotations


def normalize_handle(raw: str) -> str | None:
    """`@Name`, `x.com/name`, `https://twitter.com/name/` → `name`; не аккаунт → None."""
    raise NotImplementedError


def parse_handles(text: str) -> tuple[list[str], list[str]]:
    """Разбить текст на токены; вернуть (нормализованные хэндлы без повторов, мусор)."""
    raise NotImplementedError


def classify_sample(text: str) -> tuple[str, str]:
    """Пример стиля → (вид, значение): `handle` (@имя или ссылка на профиль),
    `url` (ссылка на пост), `text` (остальное)."""
    raise NotImplementedError


def parse_usd_to_micro(text: str) -> int | None:
    """«7.5» или «$7,5» → микродоллары; ≤0, не число или >1000$ → None."""
    raise NotImplementedError


def parse_timezone(text: str) -> str | None:
    """Название пояса IANA → оно же; неизвестное → None."""
    raise NotImplementedError
