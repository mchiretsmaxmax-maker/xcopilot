"""Адаптер deepseek."""

from __future__ import annotations

import httpx2

from xcopilot.llm.providers.base import ProviderResult, TextRequest


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, api_key: str, *, http_client: httpx2.AsyncClient | None = None) -> None:
        raise NotImplementedError

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        raise NotImplementedError
