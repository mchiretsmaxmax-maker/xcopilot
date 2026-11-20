"""fix/deepseek-balance: 402 — «кончились деньги», баланс DeepSeek без живой сети."""

from __future__ import annotations

import httpx
import pytest

from xcopilot.llm.balance import deepseek_balance_usd
from xcopilot.llm.errors import (
    BalanceExhausted,
    PermanentProviderError,
    ProviderUnavailable,
    TransientProviderError,
    status_error,
)

KEY = "sk-test-not-a-real-key"


def test_402_is_balance_exhausted_not_permanent() -> None:
    err = status_error("deepseek", 402)
    assert isinstance(err, BalanceExhausted)
    assert isinstance(err, ProviderUnavailable)
    assert not isinstance(err, PermanentProviderError | TransientProviderError)
    assert err.short() == "BalanceExhausted 402"


def _http(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("infos", "usd"),
    [
        ([{"currency": "CNY", "total_balance": "18.00"}], 2.5),
        ([{"currency": "USD", "total_balance": "0.00"}], 0.0),
        (
            [
                {"currency": "USD", "total_balance": "1.50"},
                {"currency": "CNY", "total_balance": "7.20"},
            ],
            2.5,
        ),
    ],
)
async def test_balance_usd_with_cny_rate(infos: list[dict[str, str]], usd: float) -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"is_available": True, "balance_infos": infos})

    async with _http(handle) as http:
        got = await deepseek_balance_usd(http, KEY, cny_per_usd=7.2)
    assert got == pytest.approx(usd)
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


async def test_balance_failure_returns_none() -> None:
    async with _http(lambda r: httpx.Response(401, json={})) as http:
        assert await deepseek_balance_usd(http, KEY, cny_per_usd=7.2) is None

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    async with _http(boom) as http:
        assert await deepseek_balance_usd(http, KEY, cny_per_usd=7.2) is None
    async with _http(lambda r: httpx.Response(200, text="не json")) as http:
        assert await deepseek_balance_usd(http, KEY, cny_per_usd=7.2) is None
