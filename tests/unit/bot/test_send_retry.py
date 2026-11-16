"""Аудит D (AUD-004): повтор отправки в Telegram — `send_with_retry`."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from xcopilot.bot.notify import DeliveryFailed, send_with_retry
from xcopilot.errors import ExternalServiceError

METHOD = SendMessage(chat_id=1, text="x")


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def bot_with(*outcomes: Any) -> Any:
    bot = AsyncMock()
    bot.send_message.side_effect = list(outcomes)
    return bot


async def test_send_retries_on_retry_after() -> None:
    sleep = Sleeps()
    ok = object()
    bot = bot_with(TelegramRetryAfter(METHOD, "flood", retry_after=2), ok)
    result = await send_with_retry(bot, 1, "привет", sleep=sleep, parse_mode="HTML")
    assert result is ok
    assert sleep.calls == [2]
    assert bot.send_message.await_count == 2
    bot.send_message.assert_awaited_with(1, "привет", parse_mode="HTML")


async def test_send_retries_on_network_error_then_gives_up() -> None:
    sleep = Sleeps()
    bot = bot_with(*[TelegramNetworkError(METHOD, "net") for _ in range(3)])
    with pytest.raises(DeliveryFailed):
        await send_with_retry(bot, 1, "привет", sleep=sleep)
    assert issubclass(DeliveryFailed, ExternalServiceError)
    assert bot.send_message.await_count == 3
    assert sleep.calls == [1, 2, 4]


async def test_send_does_not_retry_bad_request() -> None:
    sleep = Sleeps()
    bot = bot_with(TelegramBadRequest(METHOD, "Bad Request: chat not found"))
    with pytest.raises(DeliveryFailed):
        await send_with_retry(bot, 1, "привет", sleep=sleep)
    assert bot.send_message.await_count == 1
    assert sleep.calls == []


async def test_retry_after_is_capped() -> None:
    sleep = Sleeps()
    ok = object()
    bot = bot_with(TelegramRetryAfter(METHOD, "flood", retry_after=600), ok)
    assert await send_with_retry(bot, 1, "привет", sleep=sleep) is ok
    assert sleep.calls == [30]
