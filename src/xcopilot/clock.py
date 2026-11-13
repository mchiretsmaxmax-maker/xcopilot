"""Время (заглушка до зелёного)."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        raise NotImplementedError
