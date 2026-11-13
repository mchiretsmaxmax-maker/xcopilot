"""Журнал вызовов моделей."""

from __future__ import annotations

import builtins
from datetime import datetime
from typing import Any
from uuid import UUID

from xcopilot.db.models import LlmCall
from xcopilot.db.repos.base import TenantRepo


class LlmCallsRepo(TenantRepo[LlmCall]):
    model = LlmCall

    async def log(self, *, ts: datetime, **values: Any) -> UUID:
        raise NotImplementedError

    async def for_entity(self, entity_type: str, entity_id: UUID) -> builtins.list[LlmCall]:
        raise NotImplementedError
