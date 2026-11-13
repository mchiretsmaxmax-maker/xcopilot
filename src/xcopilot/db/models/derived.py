"""Выводы (производное): паспорт стиля, уроки, факты, инсайты. Всегда ссылаются на источник."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from xcopilot.db.models.base import Base, account_fk, created_at, jsonb, one_of, uuid_pk

PASSPORT_STATUSES = ("draft", "active", "archived")
PASSPORT_AUTHORS = ("llm", "user")
LESSON_KINDS = ("do", "dont", "pattern")
LESSON_STATUSES = ("proposed", "accepted", "rejected", "retired")


class StylePassport(Base):
    __tablename__ = "style_passports"
    __table_args__ = (
        UniqueConstraint("account_id", "version"),
        Index(
            "uq_style_passports_one_active",
            "account_id",
            unique=True,
            postgresql_where=sql_text("status = 'active'"),
        ),
        one_of("status", PASSPORT_STATUSES),
        one_of("created_by", PASSPORT_AUTHORS),
        {"comment": "Паспорт голоса аккаунта; версии, активна ровно одна"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    version: Mapped[int] = mapped_column(comment="Номер версии в аккаунте, с 1")
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'draft'"), comment="draft / active / archived"
    )
    body: Mapped[dict[str, Any]] = jsonb("Паспорт: тон, приёмы, лексика (схема pydantic)")
    hard_rules: Mapped[list[Any]] = jsonb("Жёсткие правила, проверяемые кодом", "[]")
    source_refs: Mapped[list[Any]] = jsonb("ID источников, на которых построен паспорт", "[]")
    created_by: Mapped[str] = mapped_column(comment="Кто создал: llm / user")
    created_at: Mapped[datetime] = created_at()


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        Index("ix_lessons_account_id_status", "account_id", "status"),
        one_of("kind", LESSON_KINDS),
        one_of("status", LESSON_STATUSES),
        {"comment": "Правила из правок и отказов владельца"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(comment="do — делать, dont — не делать, pattern — приём")
    text: Mapped[str] = mapped_column(comment="Формулировка урока")
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'proposed'"), comment="proposed / accepted / rejected / retired"
    )
    evidence: Mapped[list[Any]] = jsonb("ID ревью, из которых выведен урок", "[]")
    weight: Mapped[float] = mapped_column(
        server_default=sql_text("1.0"), comment="Вес урока при сборке инструкций"
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), comment="Кто принял решение по уроку"
    )
    decided_at: Mapped[datetime | None] = mapped_column(comment="Когда принято решение (UTC)")
    created_at: Mapped[datetime] = created_at()


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_account_id_expires_at", "account_id", "expires_at"),
        {"comment": "Проверяемые факты с источником и сроком годности"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    statement: Mapped[str] = mapped_column(comment="Утверждение")
    value: Mapped[str | None] = mapped_column(comment="Значение (число или текст)")
    unit: Mapped[str | None] = mapped_column(comment="Единица измерения")
    source_url: Mapped[str] = mapped_column(comment="Где проверено")
    observed_at: Mapped[datetime] = mapped_column(comment="Когда факт был верен (UTC)")
    expires_at: Mapped[datetime | None] = mapped_column(
        comment="После этого момента не использовать"
    )
    created_at: Mapped[datetime] = created_at()


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (
        Index("ix_insights_account_id_kind_created_at", "account_id", "kind", "created_at"),
        {"comment": "Выводы аналитики со ссылками на данные"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(comment="Тип вывода, например best_time или format_lift")
    body: Mapped[dict[str, Any]] = jsonb("Содержимое вывода (схема pydantic)")
    source_refs: Mapped[list[Any]] = jsonb("Ссылки на данные, из которых сделан вывод", "[]")
    confidence: Mapped[float | None] = mapped_column(comment="Уверенность 0..1")
    valid_until: Mapped[datetime | None] = mapped_column(comment="До какого момента актуален")
    created_at: Mapped[datetime] = created_at()
