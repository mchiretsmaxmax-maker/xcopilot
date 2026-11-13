"""Базовый класс моделей и общие колонки.

Правила схемы (ТЗ 0.4): ключи UUIDv7 (`DEFAULT uuidv7()`), `account_id NOT NULL` в каждой
бизнес-таблице, время — timestamptz (UTC), деньги — BIGINT микродоллары, у каждой таблицы
и колонки есть комментарий (COMMENT ON).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """База всех моделей."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        uuid.UUID: Uuid(),
    }


def uuid_pk(comment: str = "Идентификатор (UUIDv7, упорядочен по времени)") -> Mapped[uuid.UUID]:
    """Первичный ключ UUIDv7, генерируется БД."""
    return mapped_column(primary_key=True, server_default=text("uuidv7()"), comment=comment)


def account_fk(comment: str = "Аккаунт-владелец (тенант)") -> Mapped[uuid.UUID]:
    """Обязательная ссылка на тенанта."""
    return mapped_column(ForeignKey("accounts.id"), nullable=False, comment=comment)


def created_at(comment: str = "Когда создано (UTC)") -> Mapped[datetime]:
    return mapped_column(server_default=func.now(), nullable=False, comment=comment)


def updated_at(comment: str = "Когда изменено (UTC)") -> Mapped[datetime]:
    return mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False, comment=comment
    )


def jsonb(comment: str, default: str = "{}") -> Mapped[Any]:
    """JSONB NOT NULL с серверным значением по умолчанию ('{}' или '[]')."""
    return mapped_column(
        JSONB, nullable=False, server_default=text(f"'{default}'::jsonb"), comment=comment
    )


def one_of(column: str, values: Iterable[str]) -> CheckConstraint:
    """CHECK: колонка принимает только перечисленные значения."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=f"{column}_allowed")
