"""Какие знания реально применены (ТЗ v3.1, этап 11, задача 12): детекторы на финальной ревизии.

Детекторы даёт мозг (9A/9B) через `AppliedDetector`; по умолчанию — `NoDetector` (ничего не
отмечает). Строки `knowledge_applications` ревизии 1 переносятся на финальную ревизию, и
`applied` ставится там: карточку и правку владельца видят именно её.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from xcopilot.db.uow import DbSession


@dataclass(frozen=True)
class AppliedHit:
    applied: bool
    score: float
    by: Literal["feature", "rubric"]


class AppliedDetector(Protocol):
    async def detect(
        self, session: DbSession, account_id: UUID, text: str, item_ids: Sequence[UUID]
    ) -> dict[UUID, AppliedHit]: ...


class NoDetector:
    """Заглушка до детекторов мозга: ничего не проверено."""

    async def detect(
        self, session: DbSession, account_id: UUID, text: str, item_ids: Sequence[UUID]
    ) -> dict[UUID, AppliedHit]:
        return {}


async def mark_applied(
    session: DbSession,
    account_id: UUID,
    draft_id: UUID,
    *,
    detector: AppliedDetector,
    now: datetime,
) -> int:
    raise NotImplementedError
