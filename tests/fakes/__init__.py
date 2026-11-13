"""Подделки внешних сервисов."""

from tests.fakes.clock import FakeClock
from tests.fakes.llm import FakeJudge, FakeLLM
from tests.fakes.telegram import FakeTelegram
from tests.fakes.x import FakeBudgetGuard, FakeNotifier, FakeSleep, FakeX

__all__ = [
    "FakeBudgetGuard",
    "FakeClock",
    "FakeJudge",
    "FakeLLM",
    "FakeNotifier",
    "FakeSleep",
    "FakeTelegram",
    "FakeX",
]
