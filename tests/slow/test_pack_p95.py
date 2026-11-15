# ruff: noqa: E501
"""p95 сборки пакета на 10 тыс. знаний ниже 150 мс.

Тяжёлый, вне CI: `uv run pytest -m slow tests/slow/test_pack_p95.py -s --no-cov`.
"""

from __future__ import annotations

import statistics
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_core_stand import NOW
from tests.fakes import FakeClock
from tests.pack_stand import TOPIC, embedder
from tests.retrieval_stand import Kb
from xcopilot.brain import api
from xcopilot.brain.schemas import TaskSpec

pytestmark = pytest.mark.slow

N = 10_000
RUNS = 60
KINDS = [
    ("hook_pattern", "hook"),
    ("structure", "structure"),
    ("thesis", "device"),
    ("thesis", "closer"),
    ("anti_pattern", "general"),
]


async def test_build_pack_p95_under_150ms_on_10k_items(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    acc = await Kb(session_factory).account("pack-big")
    async with session_factory() as s:
        for kind, element in KINDS:
            await s.execute(
                text(
                    "INSERT INTO knowledge_items (account_id, kind, layer, element, statement,"
                    " guidance, skeleton, target_metric, risk_class, status, origin, quality)"
                    " SELECT :a, CAST(:k AS text), 'craft', :e, 'Наблюдение о приёме номер ' || g || ' для постов', 'Совет номер ' || g || ' про сильную подачу',"
                    " CASE WHEN CAST(:k AS text) = 'structure' THEN '[a] → [b]' END, 'engagement', 'B',"
                    " 'active', 'materials', 0.3 + (g % 60) / 100.0"
                    " FROM generate_series(1, :n) g"
                ),
                {"a": acc, "k": kind, "e": element, "n": N // len(KINDS)},
            )
        await s.execute(
            text(
                "INSERT INTO knowledge_item_versions (item_id, account_id, version, changed_by,"
                " reason, statement, guidance, skeleton, detector, kind, layer, element, target_metric)"
                " SELECT id, account_id, 1, 'system', 'extracted', statement, guidance, skeleton,"
                " detector, kind, layer, element, target_metric FROM knowledge_items WHERE account_id = :a"
            ),
            {"a": acc},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_embeddings (item_id, account_id, model_id, version, vec)"
                " SELECT k.id, k.account_id, m.id, 1,"
                " (SELECT array_agg(((hashtext(k.id::text || d::text) % 1000) / 1000.0)::real)"
                "  FROM generate_series(1, 384) d)::vector"
                " FROM knowledge_items k, (SELECT id FROM embedding_models WHERE active) m"
                " WHERE k.account_id = :a"
            ),
            {"a": acc},
        )
        await s.execute(text("ANALYZE knowledge_items"))
        await s.execute(text("ANALYZE knowledge_embeddings"))
        await s.commit()
    emb = embedder()
    task = TaskSpec(format="post", lang="ru", topic=TOPIC, budget_tokens=1500)
    times: list[float] = []
    for _ in range(RUNS):
        async with session_factory() as s:
            t0 = time.perf_counter()
            pk = await api.build_pack(
                s, acc, task, embedder=emb, clock=FakeClock(NOW), record=False
            )
            times.append((time.perf_counter() - t0) * 1000)
            assert pk.items
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    print(f"p50={statistics.median(times):.0f} мс, p95={p95:.0f} мс")
    assert p95 < 150
