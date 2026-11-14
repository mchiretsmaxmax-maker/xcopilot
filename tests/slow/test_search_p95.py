"""p95 поиска на 50 тыс. кусков ниже 300 мс.

Тяжёлый, вне CI: `uv run pytest -m slow tests/slow/test_search_p95.py -s --no-cov`.
"""

from __future__ import annotations

import statistics
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeEmbedder
from tests.retrieval_stand import Kb
from xcopilot.retrieval.search import search

pytestmark = pytest.mark.slow

N = 50_000
QUERIES = 100


async def test_search_p95_under_300ms_on_50k_chunks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    kb = Kb(session_factory)
    acc = await kb.account("big")
    async with session_factory() as s:
        src = (
            await s.execute(
                text(
                    "INSERT INTO sources (account_id, kind, origin, content_hash, status) "
                    "VALUES (:a, 'own_post', 'manual', 'big', 'indexed') RETURNING id"
                ),
                {"a": acc},
            )
        ).scalar_one()
        doc = (
            await s.execute(
                text(
                    "INSERT INTO documents (account_id, source_id, text) VALUES (:a, :s, 'x') "
                    "RETURNING id"
                ),
                {"a": acc, "s": src},
            )
        ).scalar_one()
        await s.execute(text("DROP INDEX ix_embeddings_vec_hnsw"))
        await s.execute(
            text(
                "INSERT INTO chunks (account_id, document_id, idx, text, tags, lang) "
                "SELECT :a, :d, g, 'пост ' || (g % 997) || ' слово' || (g % 211) || ' тема' "
                "|| (g % 53), '{}', 'ru' FROM generate_series(1, :n) g"
            ),
            {"a": acc, "d": doc, "n": N},
        )
        await s.execute(
            text(
                "INSERT INTO embeddings (account_id, chunk_id, model_id, vec) "
                "SELECT c.account_id, c.id, m.id, "
                "(SELECT array_agg(random()::real) FROM generate_series(1, 384) WHERE c.idx > 0)"
                "::real[]::vector FROM chunks c, embedding_models m WHERE m.active"
            )
        )
        await s.execute(
            text(
                "CREATE INDEX ix_embeddings_vec_hnsw ON embeddings USING hnsw "
                "(vec vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
            )
        )
        await s.commit()
        await s.execute(text("ANALYZE chunks"))
        await s.execute(text("ANALYZE embeddings"))
        await s.commit()

    emb = FakeEmbedder()
    timings: list[float] = []
    async with session_factory() as s:
        await search(s, acc, "пост слово1 тема2", embedder=emb, k=10)  # прогрев
        for i in range(QUERIES):
            t0 = time.perf_counter()
            hits = await search(
                s, acc, f"пост {i % 997} слово{i % 211} тема{i % 53}", embedder=emb, k=10
            )
            timings.append((time.perf_counter() - t0) * 1000)
            assert hits
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    print(f"\nsearch p50={statistics.median(timings):.1f} ms, p95={p95:.1f} ms, N={N}")
    assert p95 < 300
