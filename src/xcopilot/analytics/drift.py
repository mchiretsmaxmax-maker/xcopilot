"""Алерт дрейфа голоса: brain_voice_drift вырос на 25% к первой неделе (этап 14, задача 16)."""

from __future__ import annotations

from uuid import UUID

from xcopilot.db.uow import DbSession

DRIFT_GROWTH = 0.25


async def drift_alert(session: DbSession, account_id: UUID) -> str | None:
    raise NotImplementedError
