"""Экспорт: пакет под задачу (.md/.json) и полная выгрузка базы знаний (JSONL)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

from xcopilot.brain.schemas import TaskSpec
from xcopilot.clock import Clock
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder


async def export_pack(
    session: DbSession,
    account_id: UUID,
    task: TaskSpec,
    fmt: Literal["md", "json"],
    *,
    embedder: Embedder,
    clock: Clock,
) -> bytes:
    raise NotImplementedError


def export_full(session: DbSession, account_id: UUID) -> AsyncIterator[str]:
    raise NotImplementedError
