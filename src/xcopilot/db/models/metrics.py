"""Метрики: журнал событий, снимки метрик, каталог и свёртки.

`events` и `post_metric_snapshots` — только добавление, месячные партиции по `ts`.
Представления `v_post_checkpoints` и `v_draft_funnel` создаются миграцией.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from xcopilot.db.models.base import Base, account_fk, created_at, jsonb, one_of, updated_at, uuid_pk

EVENT_ACTORS = ("user", "system", "model")
GRAINS = ("day", "week")
PARTITION_BY_TS = "RANGE (ts)"


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(server_default=sql_text("uuidv7()"), comment="Идентификатор (UUIDv7)")


def _ts(comment: str) -> Mapped[datetime]:
    return mapped_column(server_default=sql_text("now()"), comment=comment)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        PrimaryKeyConstraint("ts", "id"),
        Index("ix_events_account_id_type_ts", "account_id", "type", "ts"),
        Index("ix_events_account_id_entity", "account_id", "entity_type", "entity_id"),
        one_of("actor", EVENT_ACTORS),
        {
            "comment": "Журнал всех событий продукта; только добавление, партиции по месяцам",
            "postgresql_partition_by": PARTITION_BY_TS,
        },
    )

    id: Mapped[uuid.UUID] = _id()
    account_id: Mapped[uuid.UUID] = account_fk()
    ts: Mapped[datetime] = _ts("Когда произошло (UTC); ключ партиции")
    type: Mapped[str] = mapped_column(comment="Тип события, например review.approved")
    actor: Mapped[str] = mapped_column(comment="Кто: user / system / model")
    user_id: Mapped[uuid.UUID | None] = mapped_column(comment="Пользователь, если actor = user")
    entity_type: Mapped[str | None] = mapped_column(comment="Тип сущности, например draft")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(comment="ID сущности")
    props: Mapped[dict[str, Any]] = jsonb("Свойства события")


class PostMetricSnapshot(Base):
    __tablename__ = "post_metric_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("ts", "id"),
        Index("ix_post_metric_snapshots_account_id_post_ts", "account_id", "x_post_id", "ts"),
        {
            "comment": "Временной ряд метрик поста X; только добавление, партиции по месяцам",
            "postgresql_partition_by": PARTITION_BY_TS,
        },
    )

    id: Mapped[uuid.UUID] = _id()
    account_id: Mapped[uuid.UUID] = account_fk()
    x_post_id: Mapped[str] = mapped_column(comment="ID поста в X")
    ts: Mapped[datetime] = _ts("Когда снят снимок (UTC); ключ партиции")
    age_minutes: Mapped[int] = mapped_column(comment="Возраст поста на момент снимка, минут")
    impressions: Mapped[int | None] = mapped_column(BigInteger, comment="Показы")
    likes: Mapped[int | None] = mapped_column(comment="Лайки")
    replies: Mapped[int | None] = mapped_column(comment="Ответы")
    reposts: Mapped[int | None] = mapped_column(comment="Репосты")
    quotes: Mapped[int | None] = mapped_column(comment="Цитаты")
    bookmarks: Mapped[int | None] = mapped_column(comment="Закладки")
    url_clicks: Mapped[int | None] = mapped_column(comment="Клики по ссылке")
    profile_clicks: Mapped[int | None] = mapped_column(comment="Клики по профилю")
    raw: Mapped[dict[str, Any]] = jsonb("Ответ API как есть")


class AccountMetricSnapshot(Base):
    __tablename__ = "account_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "date"),
        {"comment": "Метрики аккаунта X по дням"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    date: Mapped[dt.date] = mapped_column(comment="День по поясу аккаунта")
    followers: Mapped[int | None] = mapped_column(comment="Подписчики")
    following: Mapped[int | None] = mapped_column(comment="Подписки")
    posts_count: Mapped[int | None] = mapped_column(comment="Всего постов")
    created_at: Mapped[datetime] = created_at()


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (
        one_of("grain", GRAINS),
        {"comment": "Каталог метрик; формулы грузятся из metrics/definitions/*.sql"},
    )

    key: Mapped[str] = mapped_column(primary_key=True, comment="Ключ метрики")
    version: Mapped[int] = mapped_column(comment="Версия формулы")
    title: Mapped[str] = mapped_column(comment="Название для людей")
    description: Mapped[str | None] = mapped_column(comment="Что показывает")
    unit: Mapped[str] = mapped_column(comment="Единица: ratio / count / micro_usd / minutes")
    grain: Mapped[str] = mapped_column(comment="Зерно: day / week")
    dimensions: Mapped[list[Any]] = jsonb("Измерения (ключи dims)", "[]")
    formula_sql: Mapped[str] = mapped_column(comment="SQL формулы (исполняется READ ONLY)")
    formula_hash: Mapped[str] = mapped_column(comment="Хеш формулы для контроля изменений")
    active: Mapped[bool] = mapped_column(
        server_default=sql_text("true"), comment="Считается ли метрика"
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MetricValue(Base):
    __tablename__ = "metric_values"
    __table_args__ = (
        PrimaryKeyConstraint("account_id", "metric_key", "grain", "period_start", "dims_hash"),
        one_of("grain", GRAINS),
        {"comment": "Суточные и недельные свёртки метрик"},
    )

    account_id: Mapped[uuid.UUID] = account_fk()
    metric_key: Mapped[str] = mapped_column(ForeignKey("metric_definitions.key"), comment="Метрика")
    grain: Mapped[str] = mapped_column(comment="day / week")
    period_start: Mapped[dt.date] = mapped_column(comment="Начало периода по поясу аккаунта")
    dims: Mapped[dict[str, Any]] = jsonb("Значения измерений")
    dims_hash: Mapped[str] = mapped_column(comment="Хеш dims для ключа")
    value: Mapped[Decimal | None] = mapped_column(Numeric, comment="Значение метрики")
    sample_size: Mapped[int] = mapped_column(comment="Размер выборки")
    definition_version: Mapped[int] = mapped_column(comment="Версия формулы, которой посчитано")
    computed_at: Mapped[datetime] = mapped_column(comment="Когда посчитано (UTC)")
