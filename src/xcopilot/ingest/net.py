"""Безопасная загрузка страниц по ссылке владельца (ТЗ v3.1, этап 7): `safe_get`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from xcopilot.ingest.errors import ParseError

Resolver = Callable[[str], Awaitable[list[str]]]
Getter = Callable[[str, int], Awaitable["Fetched"]]


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes


class FetchFailed(ParseError):
    """Страница не загрузилась."""


class BlockedAddress(FetchFailed):
    """Адрес запрещён: не http(s), внутренняя сеть, localhost."""


class TooLarge(FetchFailed):
    """Страница больше предела."""


async def safe_get(
    url: str,
    max_bytes: int = 2 * 1024 * 1024,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = 10.0,
    max_redirects: int = 3,
) -> Fetched:
    raise NotImplementedError
