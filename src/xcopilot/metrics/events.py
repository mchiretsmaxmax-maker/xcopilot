"""Типы событий продукта, схемы props и запись события в текущей транзакции."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from xcopilot.db.repos import EventsRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.errors import InvalidEventProps, UnknownEventType


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


class Props(BaseModel):
    """База схем props: лишние поля запрещены, типы строгие."""

    model_config = ConfigDict(extra="forbid", strict=True)


class NoProps(Props):
    """У события нет свойств."""


class SourceIngested(Props):
    source_id: str
    kind: str
    chunks: int


class Version(Props):
    version: int


class TopicSuggested(Props):
    origin: str


class RunStarted(Props):
    kind: str


class DraftGenerated(Props):
    variant_no: int
    revision: int
    format: str


class DraftLintFailed(Props):
    rule: str


class DraftJudged(Props):
    verdict: Literal["pass", "fail"]
    score: float


class DraftRewritten(Props):
    revision: int
    reason: str


class CardShown(Props):
    kind: str


class ReviewEdited(Props):
    edit_distance: float = Field(ge=0, le=1)


class ReviewRejected(Props):
    reason_code: str


class ReviewRegenerate(Props):
    reason_code: str | None = None


class PublicationConfirmed(Props):
    x_post_id: str
    matched_by: str


class PublicationMatched(Props):
    x_post_id: str
    score: float


class MetricsSynced(Props):
    posts: int


class LessonProposed(Props):
    kind: str


class BudgetEvent(Props):
    scope: str
    period: str
    spent_micro_usd: int
    limit_micro_usd: int


class LlmFailed(Props):
    role: str
    provider: str
    error: str


class XFailed(Props):
    endpoint: str
    http_status: int | None
    error: str


class JobDead(Props):
    kind: str
    error: str


_T = EventType
PROPS_SCHEMA: dict[EventType, type[BaseModel]] = {
    _T.SOURCE_INGESTED: SourceIngested,
    _T.PASSPORT_CREATED: Version,
    _T.PASSPORT_ACTIVATED: Version,
    _T.TOPIC_SUGGESTED: TopicSuggested,
    _T.RUN_STARTED: RunStarted,
    _T.DRAFT_GENERATED: DraftGenerated,
    _T.DRAFT_LINT_FAILED: DraftLintFailed,
    _T.DRAFT_JUDGED: DraftJudged,
    _T.DRAFT_REWRITTEN: DraftRewritten,
    _T.CARD_SHOWN: CardShown,
    _T.REVIEW_APPROVED: NoProps,
    _T.REVIEW_EDITED: ReviewEdited,
    _T.REVIEW_REJECTED: ReviewRejected,
    _T.REVIEW_REGENERATE: ReviewRegenerate,
    _T.PUBLICATION_CONFIRMED: PublicationConfirmed,
    _T.PUBLICATION_MATCHED: PublicationMatched,
    _T.METRICS_SYNCED: MetricsSynced,
    _T.LESSON_PROPOSED: LessonProposed,
    _T.LESSON_ACCEPTED: NoProps,
    _T.LESSON_REJECTED: NoProps,
    _T.BUDGET_WARNING: BudgetEvent,
    _T.BUDGET_EXCEEDED: BudgetEvent,
    _T.LLM_FAILED: LlmFailed,
    _T.X_FAILED: XFailed,
    _T.JOB_DEAD: JobDead,
}


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
    """Записать событие в текущей транзакции сессии; вернуть его id.

    Тип проверяется по EventType, props — по `PROPS_SCHEMA[type]`. Коммит и откат —
    дело вызывающего: откат бизнес-операции убирает и событие. `ts` нужен только
    для переноса и тестов; обычно время ставит БД.
    """
    try:
        event_type = EventType(type)
    except ValueError:
        raise UnknownEventType(f"неизвестный тип события: {type!r}") from None
    try:
        checked = PROPS_SCHEMA[event_type].model_validate(props or {})
    except ValidationError as exc:
        raise InvalidEventProps(f"{event_type.value}: {exc.errors(include_url=False)}") from exc
    entity_type, entity_id = entity if entity is not None else (None, None)
    return await EventsRepo(session, account_id).insert_event(
        type_=event_type.value,
        actor=actor,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        props=checked.model_dump(mode="json"),
        ts=ts,
    )
