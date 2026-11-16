"""Подтверждённое знание о голосе → новая версия паспорта (ТЗ 13, задача 4)."""

from __future__ import annotations

from uuid import UUID

from xcopilot.brain.context import BrainContext


async def apply_confirmed_voice_item(ctx: BrainContext, item_id: UUID, user_id: UUID) -> int | None:
    raise NotImplementedError
