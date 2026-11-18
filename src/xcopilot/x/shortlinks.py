"""Короткие ссылки X (t.co): раскрытие по редиректу и «в тексте только ссылки»."""

from __future__ import annotations

import httpx


async def expand_short_link(http: httpx.AsyncClient, url: str) -> str | None:
    raise NotImplementedError


def find_short_link(text: str) -> str | None:
    raise NotImplementedError


def only_short_links(text: str) -> bool:
    raise NotImplementedError
