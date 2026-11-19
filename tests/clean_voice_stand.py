"""Стенд «Чистого голоса»: свои посты без AI-шаблонов и с ними (feat/clean-voice)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text

from tests.style_stand import SessionFactory, seed_chunks

CLEAN_POSTS = [
    "shipped a small agent that triages my inbox",
    "ran the evals again, 3 of 20 still fail on dates",
    "claude code plus a cron job is most of my stack rn",
    "spent the morning reading traces from last night",
    "my agent forgot the timezone again, fixed it with one line",
    "the fix was a retry with backoff, nothing fancy",
    "wrote the spec first this time and the agent followed it",
]
# по одному шаблону на пост: тире, бинарный контраст, запрещённое слово
SLOP_POSTS = [
    "agents are the future — build now",
    "this isn't a tool. it's a teammate",
    "let's delve into memory for agents",
]


async def source_ids_of(sf: SessionFactory, chunk_ids: Sequence[UUID]) -> list[UUID]:
    """Id источников по id кусков (порядок сохранён)."""
    async with sf() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.id, d.source_id FROM chunks c"
                    " JOIN documents d ON d.id = c.document_id"
                    " WHERE c.id = ANY(:ids)"
                ),
                {"ids": list(chunk_ids)},
            )
        ).all()
    by = {r[0]: r[1] for r in rows}
    return [by[c] for c in chunk_ids]


async def seed_own(sf: SessionFactory, account_id: UUID, texts: Sequence[str]) -> list[UUID]:
    """Свои посты; вернуть id источников."""
    return await source_ids_of(sf, await seed_chunks(sf, account_id, texts))
