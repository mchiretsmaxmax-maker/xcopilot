"""Дедуп: точный по хешу нормализованного текста, близкий — по pg_trgm (сходство > 0.9)."""

from __future__ import annotations

import hashlib
from uuid import UUID

from xcopilot.db.repos import ChunksRepo
from xcopilot.db.uow import DbSession
from xcopilot.ingest.clean import normalize

NEAR_DUP_THRESHOLD = 0.9


def content_hash(text: str) -> str:
    """sha256 нормализованного текста (пробелы и служебные параметры ссылок не важны)."""
    return hashlib.sha256(normalize(text).encode()).hexdigest()


async def find_near_duplicate(
    session: DbSession,
    account_id: UUID,
    text: str,
    *,
    exclude_source_id: UUID | None = None,
    threshold: float = NEAR_DUP_THRESHOLD,
) -> UUID | None:
    """Кусок аккаунта, почти совпадающий с text (кроме кусков exclude_source_id), или None."""
    found = await ChunksRepo(session, account_id).find_similar(
        text, threshold=threshold, exclude_source_id=exclude_source_id
    )
    return found[0] if found is not None else None
