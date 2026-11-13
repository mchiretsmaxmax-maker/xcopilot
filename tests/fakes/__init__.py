"""Подделки внешних сервисов."""

from tests.fakes.clock import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.llm import FakeJudge, FakeLLM
from tests.fakes.telegram import FakeTelegram
from tests.fakes.x import FakeBudgetGuard, FakeNotifier, FakeSleep, FakeX

__all__ = [
    "FakeBudgetGuard",
    "FakeClock",
    "FakeEmbedder",
    "FakeJudge",
    "FakeLLM",
    "FakeNotifier",
    "FakeSleep",
    "FakeTelegram",
    "FakeX",
]
