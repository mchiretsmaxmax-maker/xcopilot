"""Разбор ссылок на посты и хэндлов X (x.com, twitter.com, mobile., /i/web/status/, параметры)."""

from __future__ import annotations

from xcopilot.x.models import PostRef


def parse_post_url(text: str) -> PostRef | None:
    """Первая ссылка на пост в тексте или None."""
    raise NotImplementedError


def parse_handle(text: str) -> str | None:
    """Хэндл без @ из «@name», «name» или ссылки на профиль; None, если не хэндл."""
    raise NotImplementedError


def extract_post_urls(text: str) -> list[PostRef]:
    """Все ссылки на посты в тексте по порядку, без повторов id."""
    raise NotImplementedError
