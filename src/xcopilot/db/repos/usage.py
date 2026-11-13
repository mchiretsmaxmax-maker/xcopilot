"""Расход на модели и X за период (чтение журналов `llm_calls` / `x_api_calls`)."""

from __future__ import annotations

from datetime import datetime

from xcopilot.db.models import LlmCall
from xcopilot.db.repos.base import TenantRepo


class UsageRepo(TenantRepo[LlmCall]):
    model = LlmCall

    async def llm_spend_by_role(self, start: datetime, end: datetime) -> dict[str, int]:
        """Расход на модели по ролям за [start, end), микродоллары."""
        raise NotImplementedError

    async def x_spend(self, start: datetime, end: datetime) -> int:
        """Расход на X API за [start, end), микродоллары."""
        raise NotImplementedError
