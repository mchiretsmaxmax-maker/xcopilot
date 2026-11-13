"""Повторы временных сбоев и предохранитель на провайдера."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from xcopilot.clock import Clock

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]], policy: RetryPolicy, *, sleep: Sleep = asyncio.sleep
) -> T:
    raise NotImplementedError


class CircuitBreaker:
    def __init__(
        self, name: str, clock: Clock, *, failure_threshold: int = 5, reset_after: float = 30.0
    ) -> None:
        self.name = name

    @property
    def state(self) -> Literal["closed", "open", "half_open"]:
        raise NotImplementedError

    def before_call(self) -> None:
        raise NotImplementedError

    def on_success(self) -> None:
        raise NotImplementedError

    def on_failure(self) -> None:
        raise NotImplementedError
