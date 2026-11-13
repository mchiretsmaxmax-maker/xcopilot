"""Дедуп: точный по хешу нормализованного текста, близкий — по pg_trgm."""

from __future__ import annotations

from uuid import UUID

from xcopilot.db.uow import DbSession

NEAR_DUP_THRESHOLD = 0.9


def content_hash(text: str) -> str:
    raise NotImplementedError


async def find_near_duplicate(
    session: DbSession,
    account_id: UUID,
    text: str,
    *,
    exclude_source_id: UUID | None = None,
    threshold: float = NEAR_DUP_THRESHOLD,
) -> UUID | None:
    raise NotImplementedError
