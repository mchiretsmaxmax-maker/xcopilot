"""Баланс DeepSeek (GET /user/balance): сумма в долларах по курсу CNY из конфига."""

from __future__ import annotations

import httpx

BALANCE_URL = "https://api.deepseek.com/user/balance"


async def deepseek_balance_usd(
    http: httpx.AsyncClient, api_key: str, *, cny_per_usd: float, url: str = BALANCE_URL
) -> float | None:
    raise NotImplementedError
