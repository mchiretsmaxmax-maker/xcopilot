"""Гибридный поиск: вектор (HNSW) + полнотекст (tsvector) → RRF → MMR."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from xcopilot.db.repos.retrieval import Hit, HitFilters
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


async def vector_search(
    session: DbSession,
    account_id: UUID,
    query_vec: Sequence[float],
    *,
    kinds: Sequence[str] | None = None,
    filters: SearchFilters | None = None,
    limit: int = 50,
) -> list[Hit]:
    raise NotImplementedError


async def keyword_search(
    session: DbSession,
    account_id: UUID,
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    filters: SearchFilters | None = None,
    limit: int = 50,
) -> list[Hit]:
    raise NotImplementedError


async def explain_vector_search(
    session: DbSession,
    account_id: UUID,
    query_vec: Sequence[float],
    *,
    kinds: Sequence[str] | None = None,
    filters: SearchFilters | None = None,
) -> str:
    raise NotImplementedError


def rrf(result_lists: Sequence[Sequence[Hit]], k: int = 60) -> list[Hit]:
    raise NotImplementedError


def mmr(hits: Sequence[Hit], lambda_: float = 0.7, k: int = 10) -> list[Hit]:
    raise NotImplementedError


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
    raise NotImplementedError
