"""Повторы временных сбоев и предохранитель на провайдера.

Повторяются только `TransientProviderError` (429, 5xx, таймауты, обрывы): до `retries` раз,
пауза — экспонента с разбросом, не выше `max_delay`. Ошибки запроса (4xx) не повторяются.

Предохранитель размыкается после `failure_threshold` неудачных вызовов подряд (неудача — вызов,
не прошедший и после повторов). Пока разомкнут — `CircuitOpen` без обращения к провайдеру.
Через `reset_after` секунд пропускается один пробный вызов: успех замыкает, неудача снова
размыкает на ту же паузу.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from xcopilot.clock import Clock
from xcopilot.llm.errors import CircuitOpen, TransientProviderError

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.5


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]], policy: RetryPolicy, *, sleep: Sleep = asyncio.sleep
) -> T:
    """Вызвать fn; на временных сбоях повторить до policy.retries раз."""
    retrying = AsyncRetrying(
        stop=stop_after_attempt(policy.retries + 1),
        wait=wait_exponential_jitter(
            multiplier=policy.base_delay, max=policy.max_delay, jitter=policy.jitter
        ),
        retry=retry_if_exception_type(TransientProviderError),
        sleep=sleep,
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            return await fn()
    raise AssertionError("недостижимо")  # pragma: no cover


State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(
        self, name: str, clock: Clock, *, failure_threshold: int = 5, reset_after: float = 30.0
    ) -> None:
        self.name = name
        self._clock = clock
        self._threshold = failure_threshold
        self._reset_after = timedelta(seconds=reset_after)
        self._failures = 0
        self._opened_at: datetime | None = None
        self._probing = False

    @property
    def state(self) -> State:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self._probing else "open"

    def before_call(self) -> None:
        """Разрешить вызов или бросить CircuitOpen."""
        if self._opened_at is None:
            return
        if self._probing or self._clock.now() < self._opened_at + self._reset_after:
            raise CircuitOpen(f"{self.name}: предохранитель разомкнут")
        self._probing = True

    def on_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probing = False

    def on_failure(self) -> None:
        self._failures += 1
        if self._probing or self._failures >= self._threshold:
            self._opened_at = self._clock.now()
            self._probing = False
