"""Экспорт: пакет под задачу (.md/.json) и полная выгрузка базы знаний (JSONL)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

from xcopilot.brain import api, render
from xcopilot.brain.schemas import TaskSpec
from xcopilot.clock import Clock
from xcopilot.db.repos.pack import PackRepo
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
    pack = await api.build_pack(
        session, account_id, task, embedder=embedder, clock=clock, consumer="export"
    )
    text = render.to_markdown(pack) if fmt == "md" else render.to_json(pack)
    return text.encode("utf-8")


async def export_full(session: DbSession, account_id: UUID) -> AsyncIterator[str]:
    """Вся база знаний аккаунта: одна карточка (`ItemCard`) в строке, без перевода строки."""
    for row in await PackRepo(session, account_id).all_cards():
        yield api.card_of(row).model_dump_json()
