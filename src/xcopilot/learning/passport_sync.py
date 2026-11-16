"""Подтверждённое знание о голосе → новая версия паспорта (ТЗ 13, задача 4).

Жёсткое правило — запрет фразы: знание `anti_pattern` слоя `voice` с фразой в кавычках в
утверждении. Остальное знание о голосе живёт в базе и идёт в пакет, паспорт не трогает.
"""

from __future__ import annotations

from uuid import UUID

from xcopilot.brain import owner
from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.quality.knowledge import phrases_of
from xcopilot.style.service import add_banned_phrases_in_session


async def apply_confirmed_voice_item(ctx: BrainContext, item_id: UUID, user_id: UUID) -> int | None:
    """Номер новой активной версии паспорта; None — не жёсткое правило, не подтверждено, повтор."""
    await owner.require_owner(ctx, user_id)
    row = await BrainRepo(ctx.session, ctx.account_id).item(item_id)
    if (
        row is None
        or row["origin"] != "edits"
        or row["layer"] != "voice"
        or row["kind"] != "anti_pattern"
        or row["status"] != "active"
        or row["owner_state"] not in ("confirmed", "pinned")
    ):
        return None
    phrases = list(phrases_of(row["statement"]))
    if not phrases:
        return None
    version = await add_banned_phrases_in_session(
        ctx.session, ctx.account_id, phrases, user_id=user_id
    )
    return None if version is None else version.version
