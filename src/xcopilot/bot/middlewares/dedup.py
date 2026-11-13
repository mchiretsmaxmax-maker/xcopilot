"""Дедуп входящих обновлений по update_id."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

log = structlog.get_logger()


class DedupMiddleware(BaseMiddleware):
    """Пропускает обновление, если update_id уже обрабатывался (память процесса)."""

    def __init__(self, capacity: int = 10_000) -> None:
        self._seen: OrderedDict[int, None] = OrderedDict()
        self._capacity = capacity

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            if event.update_id in self._seen:
                log.info("bot_duplicate_update", update_id=event.update_id)
                return None
            self._seen[event.update_id] = None
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
        return await handler(event, data)
