"""Клиент X API v2 — только GET. Методов записи (POST/PUT/DELETE) здесь нет и быть не должно.

Каждый HTTP-запрос проходит через `CallMeter` (бюджет до запроса, журнал после).
На 429 ждёт до времени из заголовка `x-rate-limit-reset` и повторяет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

import httpx

from xcopilot.clock import Clock
from xcopilot.x.models import XPost, XUser

API_BASE = "https://api.x.com"

EP_POST = "GET /2/tweets/:id"
EP_POSTS = "GET /2/tweets"
EP_USER_BY_HANDLE = "GET /2/users/by/username/:username"
EP_ME = "GET /2/users/me"
EP_USER_POSTS = "GET /2/users/:id/tweets"

TokenProvider = Callable[[], Awaitable[str]]
Sleep = Callable[[float], Awaitable[None]]


class CallMeter(Protocol):
    """Учёт одного HTTP-вызова: `before` — проверка бюджета, `after` — журнал."""

    async def before(
        self, endpoint: str, resource_ids: Sequence[str] | None, max_resources: int
    ) -> None: ...

    async def after(
        self, endpoint: str, resource_ids: Sequence[str], http_status: int | None, ok: bool
    ) -> None: ...


class XApiV2:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token: TokenProvider,
        *,
        clock: Clock,
        sleep: Sleep,
        max_rate_limit_waits: int = 3,
    ) -> None:
        self._http = http
        self._token = token
        self._clock = clock
        self._sleep = sleep
        self._max_waits = max_rate_limit_waits

    async def get_post(self, post_id: str, *, meter: CallMeter | None = None) -> XPost:
        raise NotImplementedError

    async def get_posts(self, ids: Sequence[str], *, meter: CallMeter | None = None) -> list[XPost]:
        raise NotImplementedError

    async def get_user(self, handle: str, *, meter: CallMeter | None = None) -> XUser:
        raise NotImplementedError

    async def get_me(self, *, meter: CallMeter | None = None) -> XUser:
        raise NotImplementedError

    async def get_own_posts(
        self,
        user_id: str,
        since_id: str | None = None,
        limit: int = 100,
        *,
        meter: CallMeter | None = None,
    ) -> list[XPost]:
        """Свои посты (новые первыми) с публичными и непубличными метриками, постранично."""
        raise NotImplementedError
