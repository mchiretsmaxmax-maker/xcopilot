"""Пачки (ТЗ v3.1, этап 7): альбом или серия сообщений за окно → один вызов `flush`.

Каждое новое сообщение по ключу (чат) сдвигает окно: пачка уходит, когда `window_s`
секунд ничего не приходило, или сразу, когда набралось `max_items`. Сбой `flush`
пишется в лог и не мешает следующим пачкам.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable

import structlog

log = structlog.get_logger()


class Debouncer[K: Hashable, T]:
    def __init__(
        self,
        flush: Callable[[K, list[T]], Awaitable[None]],
        *,
        window_s: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_items: int = 100,
    ) -> None:
        if window_s < 0 or max_items < 1:
            raise ValueError("window_s >= 0 и max_items >= 1")
        self._flush = flush
        self.window_s = window_s
        self._sleep = sleep
        self._max = max_items
        self._items: dict[K, list[T]] = {}
        self._timers: dict[K, asyncio.Task[None]] = {}
        self._running: set[asyncio.Task[None]] = set()

    def pending(self, key: K) -> int:
        """Сколько элементов ждёт по ключу."""
        return len(self._items.get(key, ()))

    async def add(self, key: K, item: T) -> None:
        """Добавить элемент в пачку ключа и сдвинуть окно."""
        self._items.setdefault(key, []).append(item)
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        if len(self._items[key]) >= self._max:
            self._start(self._run(key))
            return
        task = self._start(self._wait_then_run(key))
        self._timers[key] = task

    def _start(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.ensure_future(coro)
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return task

    async def _wait_then_run(self, key: K) -> None:
        await self._sleep(self.window_s)
        self._timers.pop(key, None)
        await self._run(key)

    async def _run(self, key: K) -> None:
        items = self._items.pop(key, [])
        if not items:
            return
        try:
            await self._flush(key, items)
        except Exception:
            log.exception("debouncer_flush_failed", items=len(items))

    async def drain(self) -> None:
        """Дождаться всех запланированных пачек (для остановки и тестов)."""
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)
