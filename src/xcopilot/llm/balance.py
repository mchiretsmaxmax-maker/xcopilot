"""Баланс DeepSeek (GET /user/balance): сумма в долларах по курсу CNY из конфига.

Ключ уходит только в заголовке запроса; в журнал не пишем ни ключ, ни тело ответа.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger()

BALANCE_URL = "https://api.deepseek.com/user/balance"
TIMEOUT = 10.0


def _usd(infos: Any, cny_per_usd: float) -> float | None:
    if not isinstance(infos, list):
        return None
    total = 0.0
    for info in infos:
        if not isinstance(info, dict):
            continue
        try:
            amount = float(info.get("total_balance", 0))
        except (TypeError, ValueError):
            continue
        currency = str(info.get("currency", "")).upper()
        if currency == "USD":
            total += amount
        elif currency == "CNY" and cny_per_usd > 0:
            total += amount / cny_per_usd
    return total


async def deepseek_balance_usd(
    http: httpx.AsyncClient, api_key: str, *, cny_per_usd: float, url: str = BALANCE_URL
) -> float | None:
    """Остаток на счёте DeepSeek в долларах; сбой сети, ответ не 200 или не JSON — None."""
    try:
        resp = await http.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        log.info("deepseek_balance_failed", error=type(exc).__name__)
        return None
    if resp.status_code != 200:
        log.info("deepseek_balance_failed", status=resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        log.info("deepseek_balance_failed", error="not_json")
        return None
    return _usd(data.get("balance_infos") if isinstance(data, dict) else None, cny_per_usd)
