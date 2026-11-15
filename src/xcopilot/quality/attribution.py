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

from xcopilot.db.repos import DraftsRepo
from xcopilot.db.repos.knowledge import KnowledgeApplicationsRepo
from xcopilot.db.repos.quality import QualityRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.events import EventType, emit


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
    """Проверить знания пакета на финальной ревизии; вернуть число проверенных строк."""
    repo = QualityRepo(session, account_id)
    apps = KnowledgeApplicationsRepo(session, account_id)
    rows = await repo.applications(draft_id)
    if not rows:
        root = await repo.root_revision(draft_id)
        if root != draft_id:
            for src in await repo.applications(root):
                await apps.add(pack_id=src.pack_id, item_id=src.item_id, draft_id=draft_id,
                               slot=src.slot)  # fmt: skip
            rows = await repo.applications(draft_id)
    if not rows:
        return 0
    draft = await DraftsRepo(session, account_id).require(draft_id)
    item_ids = list(dict.fromkeys(r.item_id for r in rows))
    hits = await detector.detect(session, account_id, draft.text, item_ids)
    checked = applied = 0
    for row in rows:
        hit = hits.get(row.item_id)
        if hit is None:
            continue
        await apps.mark_checked(row.id, applied=hit.applied, score=hit.score, checked_by=hit.by,
                                at=now)  # fmt: skip
        checked += 1
        applied += int(hit.applied)
    if checked:
        await emit(
            session,
            account_id=account_id,
            type=EventType.KNOWLEDGE_APPLIED_MARKED,
            entity=("draft", draft_id),
            props={"checked": checked, "applied": applied},
        )
    return checked
