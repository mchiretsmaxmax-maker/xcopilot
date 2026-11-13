"""Производство контента: темы, запуски, черновики, ревью, публикации."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from xcopilot.db.models.base import (
    Base,
    account_fk,
    created_at,
    jsonb,
    one_of,
    updated_at,
    uuid_pk,
)
from xcopilot.db.models.knowledge import EMBEDDING_DIM
from xcopilot.errors import InvalidTransition


class DraftStatus(StrEnum):
    """Статусы черновика."""

    GENERATED = "generated"
    LINTED = "linted"
    JUDGED = "judged"
    SHOWN = "shown"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    PUBLISHED = "published"
    UNCONFIRMED = "unconfirmed"
    NEEDS_OWNER = "needs_owner"
    EXPIRED = "expired"


_S = DraftStatus
# generated → linted → judged → shown → approved | edited | rejected → published | unconfirmed;
# needs_owner — ворота качества не уверены, решает владелец; expired — устарел, не показан.
ALLOWED_TRANSITIONS: dict[DraftStatus, frozenset[DraftStatus]] = {
    _S.GENERATED: frozenset({_S.LINTED, _S.NEEDS_OWNER, _S.EXPIRED}),
    _S.LINTED: frozenset({_S.JUDGED, _S.NEEDS_OWNER, _S.EXPIRED}),
    _S.JUDGED: frozenset({_S.SHOWN, _S.NEEDS_OWNER, _S.EXPIRED}),
    _S.NEEDS_OWNER: frozenset({_S.SHOWN, _S.REJECTED, _S.EXPIRED}),
    _S.SHOWN: frozenset({_S.APPROVED, _S.EDITED, _S.REJECTED, _S.EXPIRED}),
    _S.APPROVED: frozenset({_S.PUBLISHED, _S.UNCONFIRMED}),
    _S.EDITED: frozenset({_S.PUBLISHED, _S.UNCONFIRMED}),
    _S.UNCONFIRMED: frozenset({_S.PUBLISHED, _S.EXPIRED}),
    _S.REJECTED: frozenset(),
    _S.PUBLISHED: frozenset(),
    _S.EXPIRED: frozenset(),
}


def assert_transition(old: DraftStatus | str, new: DraftStatus | str) -> None:
    """Проверить переход статуса черновика; запрещённый — InvalidTransition."""
    if DraftStatus(new) not in ALLOWED_TRANSITIONS[DraftStatus(old)]:
        raise InvalidTransition(f"черновик: переход {old} → {new} запрещён")


TOPIC_ORIGINS = ("suggested", "manual", "radar", "plan", "trend_tail")
TOPIC_STATUSES = ("new", "planned", "in_progress", "used", "dropped")
RUN_KINDS = ("post", "thread", "reply", "quote", "trend_tail")
RUN_STATUSES = ("running", "done", "failed")
REVIEW_ACTIONS = ("approve", "edit", "regenerate", "reject")
MATCHED_BY = ("user_link", "auto", "manual")


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        Index("ix_topics_account_id_status_planned_for", "account_id", "status", "planned_for"),
        one_of("origin", TOPIC_ORIGINS),
        one_of("status", TOPIC_STATUSES),
        {"comment": "Темы для постов"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    title: Mapped[str] = mapped_column(comment="Тема одной строкой")
    angle: Mapped[str | None] = mapped_column(comment="Угол подачи")
    origin: Mapped[str] = mapped_column(
        comment="Откуда: suggested / manual / radar / plan / trend_tail"
    )
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'new'"), comment="new / planned / in_progress / used / dropped"
    )
    planned_for: Mapped[datetime | None] = mapped_column(comment="На когда запланирована (UTC)")
    dedupe_vec: Mapped[Any | None] = mapped_column(
        Vector(EMBEDDING_DIM), comment="Вектор темы для поиска повторов"
    )
    created_at: Mapped[datetime] = created_at()


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        Index("ix_generation_runs_account_id_created_at", "account_id", "created_at"),
        one_of("kind", RUN_KINDS),
        one_of("status", RUN_STATUSES),
        {"comment": "Один запуск генерации черновиков"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id"), comment="Тема (у ответов может не быть)"
    )
    kind: Mapped[str] = mapped_column(comment="post / thread / reply / quote / trend_tail")
    context_pack: Mapped[dict[str, Any]] = jsonb("ID кусков и уроков, попавших в контекст")
    passport_version: Mapped[int | None] = mapped_column(comment="Версия паспорта стиля")
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompts.id"), comment="Версия промпта писателя"
    )
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'running'"), comment="running / done / failed"
    )
    error: Mapped[str | None] = mapped_column(comment="Текст ошибки (без секретов)")
    created_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(comment="Когда закончен (UTC)")


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        UniqueConstraint("account_id", "run_id", "variant_no", "revision"),
        Index("ix_drafts_account_id_status_created_at", "account_id", "status", "created_at"),
        Index("ix_drafts_account_id_parent_draft_id", "account_id", "parent_draft_id"),
        one_of("status", [s.value for s in DraftStatus]),
        {"comment": "Черновики и их ревизии (ревизия ссылается на родителя)"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("generation_runs.id"), comment="Запуск генерации"
    )
    variant_no: Mapped[int] = mapped_column(
        server_default=sql_text("1"), comment="Номер варианта в запуске, с 1"
    )
    revision: Mapped[int] = mapped_column(
        server_default=sql_text("1"), comment="Номер ревизии варианта, с 1"
    )
    parent_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("drafts.id"), comment="Предыдущая ревизия"
    )
    format: Mapped[str] = mapped_column(comment="Формат: post / thread / reply / quote и т. п.")
    text: Mapped[str] = mapped_column(comment="Текст черновика")
    parts: Mapped[list[Any]] = jsonb("Части треда по порядку", "[]")
    reply_to_url: Mapped[str | None] = mapped_column(comment="Пост, на который отвечаем")
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'generated'"), comment="Статус по машине DraftStatus"
    )
    lint_report: Mapped[dict[str, Any] | None] = mapped_column(comment="Отчёт линтера")
    judge_verdict: Mapped[dict[str, Any] | None] = mapped_column(comment="Вердикт судьи")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        Index("ix_reviews_account_id_draft_id", "account_id", "draft_id"),
        one_of("action", REVIEW_ACTIONS),
        {"comment": "Решения владельца по черновикам; пары «до/после»"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drafts.id"), comment="Черновик")
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), comment="Кто решил")
    action: Mapped[str] = mapped_column(comment="approve / edit / regenerate / reject")
    reason_code: Mapped[str | None] = mapped_column(comment="Код причины отказа или правки")
    text_before: Mapped[str] = mapped_column(comment="Текст до решения")
    text_after: Mapped[str | None] = mapped_column(comment="Текст после правки")
    edit_distance: Mapped[float | None] = mapped_column(
        comment="Нормированная дистанция до/после, 0..1"
    )
    created_at: Mapped[datetime] = created_at()


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("account_id", "x_post_id"),
        UniqueConstraint("account_id", "draft_id"),
        one_of("matched_by", MATCHED_BY),
        {"comment": "Связь черновика с опубликованным постом X"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drafts.id"), comment="Черновик")
    x_post_id: Mapped[str] = mapped_column(comment="ID поста в X")
    url: Mapped[str] = mapped_column(comment="Ссылка на пост")
    published_at: Mapped[datetime] = mapped_column(comment="Когда опубликован (UTC)")
    matched_by: Mapped[str] = mapped_column(comment="Как связали: user_link / auto / manual")
    match_score: Mapped[float | None] = mapped_column(comment="Похожесть текста при автосверке")
    final_text: Mapped[str] = mapped_column(comment="Текст, который вышел в X")
    created_at: Mapped[datetime] = created_at()
