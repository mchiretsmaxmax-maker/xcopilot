"""Решения владельца о знаниях (ТЗ v3.1, 9C): подтвердить, изменить, закрепить, отключить,
удалить, добавить своё, развести противоречие, убрать доказательство.

Все функции проверяют, что `user_id` — владелец аккаунта (`NotOwner`), пишут событие
`knowledge.owner_decision` и меняют базу тем же путём, что и автоматика (`lifecycle.apply` с
`user_id` → actor owner).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from xcopilot.brain.context import BrainContext

SUPPRESS_DAYS = 30
Resolution = Literal["first", "second", "scope"]


class NotOwner(PermissionError):
    """Менять знания может только владелец аккаунта."""


class InvalidItem(ValueError):
    """Текст знания не прошёл проверку схемы (длина, ссылки, обращения к модели)."""


async def require_owner(ctx: BrainContext, user_id: UUID) -> None:
    raise NotImplementedError


async def confirm(ctx: BrainContext, item_id: UUID, user_id: UUID) -> None:
    raise NotImplementedError


async def confirm_batch(ctx: BrainContext, item_ids: Sequence[UUID], user_id: UUID) -> int:
    raise NotImplementedError


async def reject(ctx: BrainContext, item_id: UUID, user_id: UUID) -> None:
    raise NotImplementedError


async def is_suppressed(ctx: BrainContext, statement: str) -> bool:
    raise NotImplementedError


async def edit(
    ctx: BrainContext, item_id: UUID, user_id: UUID, statement: str, guidance: str
) -> int:
    raise NotImplementedError


async def pin(ctx: BrainContext, item_id: UUID, user_id: UUID) -> None:
    raise NotImplementedError


async def disable(
    ctx: BrainContext, item_id: UUID, user_id: UUID, *, not_mine: bool = False
) -> None:
    raise NotImplementedError


async def remove_evidence(ctx: BrainContext, evidence_id: UUID, user_id: UUID) -> None:
    raise NotImplementedError


async def add_manual(ctx: BrainContext, user_id: UUID, text: str) -> UUID:
    raise NotImplementedError


async def resolve_conflict(
    ctx: BrainContext, link_id: UUID, user_id: UUID, resolution: Resolution
) -> None:
    raise NotImplementedError


async def autonomy(ctx: BrainContext) -> Literal["auto", "manual"]:
    raise NotImplementedError
