"""Доступ к знаниям для любой модели: пакет под задачу, поиск, карточка, объяснение (этап 9B)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from xcopilot.brain.schemas import ItemCard, KnowledgePack, PackExplanation, PackItem, TaskSpec
from xcopilot.clock import Clock
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.writer.probation import ProbationPicker


async def build_pack(
    session: DbSession,
    account_id: UUID,
    task: TaskSpec,
    *,
    embedder: Embedder,
    clock: Clock,
    consumer: str = "writer",
    probation: ProbationPicker | None = None,
    record: bool = True,
) -> KnowledgePack:
    raise NotImplementedError


async def load_pack(session: DbSession, account_id: UUID, pack_id: UUID) -> KnowledgePack:
    """Пакет, восстановленный по сохранённым id и версиям (текст — как на момент выдачи)."""
    raise NotImplementedError


async def search(
    session: DbSession,
    account_id: UUID,
    query: str,
    *,
    embedder: Embedder,
    filters: Mapping[str, Any] | None = None,
    k: int = 10,
) -> list[PackItem]:
    raise NotImplementedError


async def get_item(session: DbSession, account_id: UUID, item_id: UUID) -> ItemCard:
    raise NotImplementedError


async def explain(session: DbSession, account_id: UUID, pack_id: UUID) -> PackExplanation:
    raise NotImplementedError
