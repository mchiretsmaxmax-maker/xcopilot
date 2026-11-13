"""Интерфейс времени: в прикладном коде время берётся только через Clock."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Настоящее системное время (UTC)."""

    def now(self) -> datetime:
        return datetime.now(UTC)
