"""Стартовое наполнение (ТЗ v3.1, 9A, задача 9): анти-AI правила как знания класса C."""

from __future__ import annotations

from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.db.uow import DbSession


async def load_global_seed(session: DbSession) -> int:
    """Загрузить анти-AI правила в системный аккаунт один раз; вернуть число новых знаний."""
    raise NotImplementedError


async def copy_seed_to_account(ctx: BrainContext) -> list[UUID]:
    """Копии общих знаний сида в аккаунт: parent_item_id и априор, ослабленный вдвое."""
    raise NotImplementedError
