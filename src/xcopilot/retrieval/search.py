"""Гибридный поиск: вектор (HNSW) + полнотекст (tsvector) → RRF → MMR.

Всё детерминировано: ничьи решает id куска, поэтому один и тот же запрос к одному и тому
же состоянию базы даёт одинаковый список.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from xcopilot.db.repos.retrieval import Hit, HitFilters, RetrievalRepo
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder

__all__ = [
    "Hit",
    "SearchFilters",
    "explain_vector_search",
    "keyword_search",
    "mmr",
    "rrf",
    "search",
    "vector_search",
]

SearchFilters = HitFilters
CANDIDATES_MIN = 40
_WORD = re.compile(r"\w+", re.UNICODE)


async def vector_search(
    session: DbSession,
    account_id: UUID,
    query_vec: Sequence[float],
    *,
    kinds: Sequence[str] | None = None,
    filters: SearchFilters | None = None,
    limit: int = 50,
) -> list[Hit]:
    """Ближайшие по косинусу куски аккаунта; score = косинусная близость."""
    repo = RetrievalRepo(session, account_id)
    return await repo.vector_hits(
        query_vec, kinds=kinds, filters=filters or HitFilters(), limit=limit
    )


async def keyword_search(
    session: DbSession,
    account_id: UUID,
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    filters: SearchFilters | None = None,
    limit: int = 50,
) -> list[Hit]:
    """Куски, где есть хотя бы одно слово запроса (полнотекст, конфигурация simple)."""
    repo = RetrievalRepo(session, account_id)
    return await repo.keyword_hits(query, kinds=kinds, filters=filters or HitFilters(), limit=limit)


async def explain_vector_search(
    session: DbSession,
    account_id: UUID,
    query_vec: Sequence[float],
    *,
    kinds: Sequence[str] | None = None,
    filters: SearchFilters | None = None,
) -> str:
    """План векторного запроса (с выключенным seq scan): для проверки индекса HNSW."""
    repo = RetrievalRepo(session, account_id)
    return await repo.explain_vector(
        query_vec, kinds=kinds, filters=filters or HitFilters(), limit=50, force_index=True
    )


def rrf(result_lists: Sequence[Sequence[Hit]], k: int = 60) -> list[Hit]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + ранг) по спискам (ранг с 1); ничья — по id."""
    scores: dict[UUID, float] = {}
    first: dict[UUID, Hit] = {}
    vecs: dict[UUID, tuple[float, ...]] = {}
    for hits in result_lists:
        for rank, hit in enumerate(hits, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            first.setdefault(hit.chunk_id, hit)
            if hit.vec is not None:
                vecs.setdefault(hit.chunk_id, hit.vec)
    order = sorted(scores, key=lambda cid: (-scores[cid], str(cid)))
    return [replace(first[cid], score=scores[cid], vec=vecs.get(cid)) for cid in order]


def _similarity(a: Hit, b: Hit) -> float:
    """Косинус векторов, а если вектора нет — доля общих слов (Жаккар)."""
    if a.vec is not None and b.vec is not None and len(a.vec) == len(b.vec):
        dot = sum(x * y for x, y in zip(a.vec, b.vec, strict=True))
        norm = math.sqrt(sum(x * x for x in a.vec)) * math.sqrt(sum(y * y for y in b.vec))
        return dot / norm if norm > 0 else 0.0
    wa, wb = set(_WORD.findall(a.text.lower())), set(_WORD.findall(b.text.lower()))
    union = wa | wb
    return len(wa & wb) / len(union) if union else 0.0


def mmr(hits: Sequence[Hit], lambda_: float = 0.7, k: int = 10) -> list[Hit]:
    """Maximal Marginal Relevance: релевантность (score/max) против сходства с уже выбранными."""
    pool = sorted(hits, key=lambda h: (-h.score, str(h.chunk_id)))
    if not pool or k <= 0:
        return []
    top = pool[0].score
    rel = {h.chunk_id: (h.score / top if top > 0 else 0.0) for h in pool}
    chosen: list[Hit] = [pool.pop(0)]
    while pool and len(chosen) < k:
        best_i, best_val = 0, -math.inf
        for i, cand in enumerate(pool):
            penalty = max(_similarity(cand, c) for c in chosen)
            value = lambda_ * rel[cand.chunk_id] - (1.0 - lambda_) * penalty
            if value > best_val + 1e-12:  # при равенстве остаётся более релевантный (раньше)
                best_i, best_val = i, value
        chosen.append(pool.pop(best_i))
    return chosen


async def search(
    session: DbSession,
    account_id: UUID,
    query: str,
    *,
    embedder: Embedder,
    kinds: Sequence[str] | None = None,
    k: int = 10,
    filters: SearchFilters | None = None,
) -> list[Hit]:
    """Гибридный поиск: вектор + полнотекст → RRF → MMR; не больше k кусков аккаунта."""
    pool = max(k * 4, CANDIDATES_MIN)
    query_vec = (await asyncio.to_thread(embedder.embed, [query]))[0]
    by_vector = await vector_search(
        session, account_id, query_vec, kinds=kinds, filters=filters, limit=pool
    )
    by_keyword = await keyword_search(
        session, account_id, query, kinds=kinds, filters=filters, limit=pool
    )
    return mmr(rrf([by_vector, by_keyword])[:pool], k=k)
