"""Типы событий продукта, схемы props и запись события в текущей транзакции."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.db.uow import DbSession


class EventType(StrEnum):
    SOURCE_INGESTED = "source.ingested"
    PASSPORT_CREATED = "passport.created"
    PASSPORT_ACTIVATED = "passport.activated"
    TOPIC_SUGGESTED = "topic.suggested"
    RUN_STARTED = "run.started"
    DRAFT_GENERATED = "draft.generated"
    DRAFT_LINT_FAILED = "draft.lint_failed"
    DRAFT_JUDGED = "draft.judged"
    DRAFT_REWRITTEN = "draft.rewritten"
    CARD_SHOWN = "card.shown"
    REVIEW_APPROVED = "review.approved"
    REVIEW_EDITED = "review.edited"
    REVIEW_REJECTED = "review.rejected"
    REVIEW_REGENERATE = "review.regenerate"
    PUBLICATION_CONFIRMED = "publication.confirmed"
    PUBLICATION_MATCHED = "publication.matched"
    METRICS_SYNCED = "metrics.synced"
    LESSON_PROPOSED = "lesson.proposed"
    LESSON_ACCEPTED = "lesson.accepted"
    LESSON_REJECTED = "lesson.rejected"
    BUDGET_WARNING = "budget.warning"
    BUDGET_EXCEEDED = "budget.exceeded"
    LLM_FAILED = "llm.failed"
    X_FAILED = "x.failed"
    JOB_DEAD = "job.dead"


PROPS_SCHEMA: dict[EventType, type[BaseModel]] = {}


async def emit(
    session: DbSession,
    *,
    account_id: UUID,
    type: EventType | str,
    actor: str = "system",
    user_id: UUID | None = None,
    entity: tuple[str, UUID] | None = None,
    props: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> UUID:
    """Записать событие в текущей транзакции сессии; вернуть его id."""
    raise NotImplementedError
