"""AUD-012: эмбеддер (CPU, синхронный) вызывается не из потока цикла событий."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import knowledge, make_ctx, new_account
from tests.fakes.embedder import FakeEmbedder
from tests.integration.brain.test_owner import setup
from xcopilot.brain import detectors, extract, owner
from xcopilot.brain.context import BrainContext
from xcopilot.brain.detectors import RubricItem
from xcopilot.db.repos.brain import BrainRepo


@dataclass
class ThreadEmbedder(FakeEmbedder):
    threads: list[int] = field(default_factory=list)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.threads.append(threading.get_ident())
        return super().embed(texts)


def with_recorder(ctx: BrainContext) -> ThreadEmbedder:
    rec = ThreadEmbedder()
    ctx.embedder = rec
    return rec


def assert_off_loop(rec: ThreadEmbedder) -> None:
    assert rec.threads, "эмбеддер не вызывался"
    assert threading.get_ident() not in rec.threads


async def test_owner_add_manual_embeds_off_loop(session: AsyncSession) -> None:
    ctx, uid = await setup(session)
    await knowledge(session, ctx.account_id, embed=True)  # заводит активную модель эмбеддингов
    rec = with_recorder(ctx)
    await owner.add_manual(ctx, uid, "Короткие абзацы читаются лучше длинных")
    assert_off_loop(rec)


async def test_owner_is_suppressed_embeds_off_loop(session: AsyncSession) -> None:
    ctx, _ = await setup(session)
    rec = with_recorder(ctx)
    await owner.is_suppressed(ctx, "Любое утверждение")
    assert_off_loop(rec)


async def test_extract_embed_off_loop(session: AsyncSession) -> None:
    acc = await new_account(session)
    ctx = make_ctx(session, acc)
    iid = await knowledge(session, acc, embed=True)
    rec = with_recorder(ctx)
    await extract._embed(ctx, iid, "текст знания")
    assert_off_loop(rec)


async def test_detectors_nearest_rubrics_embed_off_loop(session: AsyncSession) -> None:
    acc = await new_account(session)
    ctx = make_ctx(session, acc)
    limit = ctx.config.detectors.rubric_max_items
    ids = [
        await knowledge(
            session, acc, statement=f"Знание номер {i} про личные цифры в первой строке"
        )
        for i in range(limit + 2)
    ]
    items = [RubricItem(i, "вопрос") for i in ids]
    rec = with_recorder(ctx)
    await detectors._nearest_rubrics(ctx, BrainRepo(session, acc), items, "текст материала")
    assert_off_loop(rec)
