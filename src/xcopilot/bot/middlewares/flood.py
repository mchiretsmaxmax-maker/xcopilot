"""Защита от флуда: не больше N обновлений на пользователя за окно."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps


class FloodMiddleware(BaseMiddleware):
    """Скользящее окно по времени из Clock; предупреждение — один раз за всплеск."""

    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps
        self._hits: dict[int, deque[float]] = {}
        self._warned: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        now = self._deps.clock.now().timestamp()
        window = self._deps.flood_window_seconds
        hits = self._hits.setdefault(user.id, deque())
        while hits and now - hits[0] >= window:
            hits.popleft()
        if len(hits) >= self._deps.flood_limit:
            if user.id not in self._warned:
                self._warned.add(user.id)
                await self._deps.bot.send_message(user.id, texts.FLOOD)
            return None
        self._warned.discard(user.id)
        hits.append(now)
        return await handler(event, data)
