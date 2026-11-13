"""Подделки внешних сервисов."""

from tests.fakes.clock import FakeClock
from tests.fakes.llm import FakeJudge, FakeLLM
from tests.fakes.telegram import FakeTelegram

__all__ = ["FakeClock", "FakeJudge", "FakeLLM", "FakeTelegram"]
