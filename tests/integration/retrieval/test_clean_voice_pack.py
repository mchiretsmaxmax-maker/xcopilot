"""«Чистый голос» в пакете писателя: примеры голоса — только свои посты без AI-шаблонов."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from tests.retrieval_stand import Kb, axis
from xcopilot.retrieval.context_pack import TaskSpec, build_pack
from xcopilot.retrieval.search import SearchFilters, vector_search

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


async def test_slop_own_posts_are_not_voice_examples(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("cv")
    await kb.passport(acc)
    clean = await kb.chunk(acc, "agents ship faster with evals", lang="en")
    slop = await kb.chunk(acc, "agents are the future — ship evals now", lang="en")
    pack = await build_pack(
        session,
        acc,
        TaskSpec(format="post", lang="en", topic="agents evals"),
        embedder=kb.embedder,
        now=NOW,
    )
    ids = [c.chunk_id for c in pack.examples]
    assert clean in ids
    assert slop not in ids


async def test_search_filter_excludes_sources(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("cv2")
    a = await kb.chunk(acc, "first", vec=axis(0))
    b = await kb.chunk(acc, "second", vec=axis(0, tilt=(1, 0.1)))
    hits = await vector_search(session, acc, axis(0), own_only=True)
    source_b = next(h.source_id for h in hits if h.chunk_id == b)
    hits = await vector_search(
        session, acc, axis(0), own_only=True, filters=SearchFilters(exclude_sources=(source_b,))
    )
    assert [h.chunk_id for h in hits] == [a]
