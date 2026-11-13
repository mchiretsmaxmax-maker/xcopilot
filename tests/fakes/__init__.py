"""Подделки внешних сервисов."""

from tests.fakes.clock import FakeClock
from tests.fakes.telegram import FakeTelegram
from tests.fakes.x import FakeBudgetGuard, FakeNotifier, FakeSleep, FakeX

__all__ = [
    "FakeBudgetGuard",
    "FakeClock",
    "FakeNotifier",
    "FakeSleep",
    "FakeTelegram",
    "FakeX",
]
