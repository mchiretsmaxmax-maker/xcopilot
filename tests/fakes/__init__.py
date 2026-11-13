"""Подделки внешних сервисов."""

from tests.fakes.clock import FakeClock
from tests.fakes.telegram import FakeTelegram

__all__ = ["FakeClock", "FakeTelegram"]
