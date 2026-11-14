"""Поиск по настоящей БД: вектор, полнотекст, области видимости, фильтры, индекс HNSW."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tests.retrieval_stand import Kb, ScriptedEmbedder, axis
from xcopilot.retrieval.search import (
    SearchFilters,
    explain_vector_search,
    keyword_search,
    search,
    vector_search,
)


async def test_vector_search_returns_semantic_match(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    near = await kb.chunk(acc, "про блобы и L2", vec=axis(0, tilt=(1, 0.1)))
    far = await kb.chunk(acc, "рецепт борща", vec=axis(5))
    mid = await kb.chunk(acc, "комиссии в сети", vec=axis(0, tilt=(1, 1.0)))
    hits = await vector_search(session, acc, axis(0), limit=10)
    assert [h.chunk_id for h in hits][:2] == [near, mid]
    assert hits[-1].chunk_id == far
    assert hits[0].score > hits[1].score > hits[2].score
    assert hits[0].vec is not None and len(hits[0].vec) == 384


async def test_keyword_search_finds_exact_ticker(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    target = await kb.chunk(acc, "Взял $DEGEN на просадке", vec=axis(10))
    for i in range(5):
        await kb.chunk(acc, f"заметка номер {i} про рынок", vec=axis(20 + i))
    embedder.vectors["$DEGEN"] = axis(300)  # вектор запроса далеко от цели
    kw = await keyword_search(session, acc, "$DEGEN")
    assert [h.chunk_id for h in kw] == [target]
    hits = await search(session, acc, "$DEGEN", embedder=embedder, k=3)
    assert hits[0].chunk_id == target


async def test_keyword_search_ignores_tsquery_syntax_in_input(
    kb: Kb, session: AsyncSession
) -> None:
    acc = await kb.account("a")
    await kb.chunk(acc, "обычный пост")
    assert await keyword_search(session, acc, "!!! ) ( & | : '") == []
    assert await keyword_search(session, acc, "") == []


async def test_search_scoped_to_account(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    mine = await kb.account("mine")
    other = await kb.account("other")
    own = await kb.chunk(mine, "пост про L2", vec=axis(0))
    await kb.chunk(other, "пост про L2 чужой", vec=axis(0))
    embedder.vectors["L2"] = axis(0)
    hits = await search(session, mine, "L2", embedder=embedder, k=10)
    assert [h.chunk_id for h in hits] == [own]
    assert [h.chunk_id for h in await keyword_search(session, mine, "L2")] == [own]
    assert [h.chunk_id for h in await vector_search(session, mine, axis(0))] == [own]


async def test_search_only_indexed_sources(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    ok = await kb.chunk(acc, "готовый пост про L2", vec=axis(0))
    await kb.chunk(acc, "дубль про L2", vec=axis(0), status="excluded")
    await kb.chunk(acc, "сбойный про L2", vec=axis(0), status="failed")
    assert [h.chunk_id for h in await vector_search(session, acc, axis(0))] == [ok]
    assert [h.chunk_id for h in await keyword_search(session, acc, "L2")] == [ok]


async def test_search_filters_by_kind_lang_date(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    jan = datetime(2026, 1, 10, tzinfo=UTC)
    jun = datetime(2026, 6, 10, tzinfo=UTC)
    own_ru = await kb.chunk(
        acc,
        "пост про L2",
        kind="own_post",
        lang="ru",
        created_at=jan,
        tags=["topic:l2"],
        vec=axis(0),
    )
    comp_en = await kb.chunk(
        acc,
        "post about L2",
        kind="competitor_post",
        lang="en",
        created_at=jun,
        tags=["topic:l2", "format:short"],
        vec=axis(0),
    )
    note_ru = await kb.chunk(
        acc, "заметка про L2", kind="note", lang="ru", created_at=jun, vec=axis(0)
    )
    embedder.vectors["L2"] = axis(0)

    async def ids(**kw: object) -> set[UUID]:
        hits = await search(session, acc, "L2", embedder=embedder, k=10, **kw)  # type: ignore[arg-type]
        return {h.chunk_id for h in hits}

    assert await ids() == {own_ru, comp_en, note_ru}
    assert await ids(kinds=["own_post"]) == {own_ru}
    assert await ids(kinds=["competitor_post", "note"]) == {comp_en, note_ru}
    assert await ids(filters=SearchFilters(lang="en")) == {comp_en}
    assert await ids(filters=SearchFilters(since=datetime(2026, 3, 1, tzinfo=UTC))) == {
        comp_en,
        note_ru,
    }
    assert await ids(filters=SearchFilters(until=datetime(2026, 3, 1, tzinfo=UTC))) == {own_ru}
    assert await ids(filters=SearchFilters(tags=("format:short",))) == {comp_en}
    assert await ids(filters=SearchFilters(tags=("topic:l2", "x:none"))) == {own_ru, comp_en}
    both = SearchFilters(lang="ru", since=datetime(2026, 3, 1, tzinfo=UTC))
    assert await ids(kinds=["note"], filters=both) == {note_ru}


async def test_search_removes_near_duplicates_via_mmr(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    for i in range(6):
        await kb.chunk(acc, f"L2 дубль {i}", vec=axis(0, tilt=(1, 0.001 * i)))
    distinct = await kb.chunk(acc, "L2 совсем другое", vec=axis(0, tilt=(2, 0.9)))
    embedder.vectors["L2"] = axis(0)
    hits = await search(session, acc, "L2", embedder=embedder, k=3)
    assert distinct in {h.chunk_id for h in hits}


async def test_search_is_deterministic(
    kb: Kb, session: AsyncSession, embedder: ScriptedEmbedder
) -> None:
    acc = await kb.account("a")
    for i in range(12):
        await kb.chunk(acc, f"L2 пост {i}", vec=axis(i % 3))
    first = await search(session, acc, "L2", embedder=embedder, k=8)
    second = await search(session, acc, "L2", embedder=embedder, k=8)
    assert [(h.chunk_id, h.score) for h in first] == [(h.chunk_id, h.score) for h in second]


async def test_search_uses_hnsw_index(kb: Kb, session: AsyncSession) -> None:
    acc = await kb.account("a")
    for i in range(30):
        await kb.chunk(acc, f"пост {i}", vec=axis(i))
    plan = await explain_vector_search(session, acc, axis(3))
    assert "ix_embeddings_vec_hnsw" in plan


async def test_removed_or_quarantined_material_not_in_voice_or_search(
    kb: Kb, session: AsyncSession
) -> None:
    """ТЗ v3.1: убранный владельцем материал не попадает в примеры голоса и в поиск."""
    from sqlalchemy import text

    acc = await kb.account("a")
    ok = await kb.chunk(acc, "свой пост про L2", vec=axis(0))
    removed = await kb.chunk(acc, "убранный пост про L2", vec=axis(0))
    jailed = await kb.chunk(acc, "пост в карантине про L2", kind="liked_sample", vec=axis(0))
    await session.execute(
        text(
            "UPDATE sources SET removed_at = now() WHERE id = (SELECT d.source_id FROM chunks c "
            "JOIN documents d ON d.id = c.document_id WHERE c.id = :c)"
        ),
        {"c": removed},
    )
    await session.execute(
        text(
            "UPDATE sources SET analysis_status = 'quarantined', quarantine_reason = 'risky' "
            "WHERE id = (SELECT d.source_id FROM chunks c JOIN documents d "
            "ON d.id = c.document_id WHERE c.id = :c)"
        ),
        {"c": jailed},
    )
    await session.commit()
    voice = await vector_search(session, acc, axis(0), own_only=True)
    assert [h.chunk_id for h in voice] == [ok]
    assert [h.chunk_id for h in await vector_search(session, acc, axis(0))] == [ok]
    assert [h.chunk_id for h in await keyword_search(session, acc, "L2")] == [ok]
