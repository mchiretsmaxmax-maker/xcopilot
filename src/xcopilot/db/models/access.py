"""Доступ и тенанты: люди, X-аккаунты, роли, зашифрованные секреты."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    LargeBinary,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from xcopilot.db.models.base import Base, account_fk, created_at, jsonb, one_of, updated_at, uuid_pk

ACCOUNT_KINDS = ("x", "global")
ACCOUNT_STATUSES = ("active", "paused", "archived")
X_READ_MODES = ("off", "owned", "lookup", "paid")
MEMBER_ROLES = ("owner", "editor", "viewer")


class User(Base):
    __tablename__ = "users"
    __table_args__ = ({"comment": "Люди в Telegram, которые работают с ботом"},)

    id: Mapped[uuid.UUID] = uuid_pk()
    tg_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, comment="ID пользователя в Telegram"
    )
    name: Mapped[str | None] = mapped_column(comment="Имя для обращения")
    is_admin: Mapped[bool] = mapped_column(
        server_default=sql_text("false"), comment="Администратор всего сервиса"
    )
    created_at: Mapped[datetime] = created_at()


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("kind", "handle"),
        one_of("kind", ACCOUNT_KINDS),
        one_of("status", ACCOUNT_STATUSES),
        one_of("x_read_mode", X_READ_MODES),
        {"comment": "X-аккаунты (тенанты); глобальные данные принадлежат аккаунту kind=global"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(
        server_default=sql_text("'x'"), comment="Тип: x — аккаунт X, global — системный"
    )
    handle: Mapped[str] = mapped_column(comment="Имя в X без @ (у системного — global)")
    x_user_id: Mapped[str | None] = mapped_column(comment="Числовой ID пользователя в X")
    niche: Mapped[str | None] = mapped_column(comment="Ниша аккаунта своими словами")
    lang: Mapped[str] = mapped_column(
        server_default=sql_text("'ru'"), comment="Основной язык постов"
    )
    tz: Mapped[str] = mapped_column(
        server_default=sql_text("'Europe/Minsk'"),
        comment="Часовой пояс IANA для отчётов и расписаний",
    )
    x_read_mode: Mapped[str] = mapped_column(
        server_default=sql_text("'owned'"), comment="Режим чтения X: off / owned / lookup / paid"
    )
    settings: Mapped[dict[str, Any]] = jsonb("Настройки аккаунта (схема pydantic)")
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'active'"), comment="Состояние: active / paused / archived"
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AccountMember(Base):
    __tablename__ = "account_members"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id"),
        Index("ix_account_members_user_id", "user_id"),
        one_of("role", MEMBER_ROLES),
        {"comment": "Кто к какому аккаунту имеет доступ и с какой ролью"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), comment="Пользователь")
    role: Mapped[str] = mapped_column(comment="Роль: owner / editor / viewer")
    created_at: Mapped[datetime] = created_at()


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("account_id", "kind"),
        {"comment": "Зашифрованные (Fernet) токены и ключи аккаунта"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(comment="Что за секрет, например x_oauth")
    ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary, comment="Шифротекст Fernet; открытый текст в БД не хранится"
    )
    key_version: Mapped[int] = mapped_column(comment="Версия ключа SECRETS_KEY для ротации")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
