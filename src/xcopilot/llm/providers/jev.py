"""Адаптер Jev (TypeSafe System One)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx2

from xcopilot.llm.providers.base import DecisionResult
from xcopilot.llm.schemas import Question


def to_jev_question(q: Question) -> dict[str, Any]:
    raise NotImplementedError


def parse_system_one(data: Mapping[str, Any]) -> DecisionResult:
    raise NotImplementedError


class JevProvider:
    name = "jev"

    def __init__(self, api_key: str, *, http_client: httpx2.AsyncClient | None = None) -> None:
        raise NotImplementedError

    async def decide(
        self, *, model: str, state: str, questions: Mapping[str, Question]
    ) -> DecisionResult:
        raise NotImplementedError
