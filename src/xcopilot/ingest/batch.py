"""Пачки (ТЗ v3.1, этап 7): альбом или серия сообщений за окно → один вызов `flush`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable


class Debouncer[K: Hashable, T]:
    def __init__(
        self,
        flush: Callable[[K, list[T]], Awaitable[None]],
        *,
        window_s: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._flush = flush
        self.window_s = window_s
        self._sleep = sleep

    async def add(self, key: K, item: T) -> None:
        raise NotImplementedError

    async def drain(self) -> None:
        raise NotImplementedError
