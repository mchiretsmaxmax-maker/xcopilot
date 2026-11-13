"""Время (заглушка до зелёного)."""
from typing import Protocol
from datetime import datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        raise NotImplementedError
