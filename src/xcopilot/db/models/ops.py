"""Эксплуатация: промпты, журналы вызовов, бюджеты, очередь задач, расписания, сессии бота."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    UniqueConstraint,
)
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
from xcopilot.db.models.metrics import PARTITION_BY_TS

CALL_STATUSES = ("ok", "error")
BUDGET_SCOPES = ("llm", "x_api", "x_lookup", "sniper")
BUDGET_PERIODS = ("day", "month")
JOB_STATUSES = ("queued", "running", "done", "dead")


class Prompt(Base):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("key", "version"),
        {"comment": "Версии промптов (глобальные)"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(comment="Ключ промпта, например writer.post")
    version: Mapped[int] = mapped_column(comment="Версия, с 1")
    role: Mapped[str] = mapped_column(comment="Роль модели: writer / judge / cheap и т. п.")
    template: Mapped[str] = mapped_column(comment="Текст шаблона")
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(
        comment="JSON-схема ожидаемого ответа"
    )
    content_hash: Mapped[str] = mapped_column(comment="Хеш шаблона и схемы")
    created_at: Mapped[datetime] = created_at()


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        PrimaryKeyConstraint("ts", "id"),
        Index("ix_llm_calls_account_id_role_ts", "account_id", "role", "ts"),
        Index("ix_llm_calls_account_id_entity", "account_id", "entity_type", "entity_id"),
        one_of("status", CALL_STATUSES),
        {
            "comment": "Журнал вызовов моделей; только добавление, партиции по месяцам",
            "postgresql_partition_by": PARTITION_BY_TS,
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        server_default=sql_text("uuidv7()"), comment="Идентификатор (UUIDv7)"
    )
    account_id: Mapped[uuid.UUID] = account_fk()
    ts: Mapped[datetime] = mapped_column(
        server_default=sql_text("now()"), comment="Когда вызвано (UTC); ключ партиции"
    )
    role: Mapped[str] = mapped_column(comment="Роль: writer / judge / cheap / embed")
    provider: Mapped[str] = mapped_column(comment="Провайдер: anthropic / deepseek / jev")
    model: Mapped[str] = mapped_column(comment="Модель у провайдера")
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prompts.id"), comment="Версия промпта"
    )
    purpose: Mapped[str] = mapped_column(comment="Зачем вызвали, например draft.generate")
    input_tokens: Mapped[int] = mapped_column(server_default=sql_text("0"), comment="Токены входа")
    cached_tokens: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Токены входа из кеша"
    )
    output_tokens: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Токены ответа"
    )
    cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, server_default=sql_text("0"), comment="Цена вызова, микродоллары"
    )
    latency_ms: Mapped[int | None] = mapped_column(comment="Длительность, мс")
    status: Mapped[str] = mapped_column(comment="ok / error")
    error: Mapped[str | None] = mapped_column(comment="Класс ошибки (без секретов)")
    entity_type: Mapped[str | None] = mapped_column(
        comment="Тип сущности, ради которой вызвали, например draft"
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(comment="ID сущности")


class XApiCall(Base):
    __tablename__ = "x_api_calls"
    __table_args__ = (
        PrimaryKeyConstraint("ts", "id"),
        Index("ix_x_api_calls_account_id_utc_day", "account_id", "utc_day"),
        one_of("status", CALL_STATUSES),
        {
            "comment": "Журнал вызовов X API; только добавление, партиции по месяцам",
            "postgresql_partition_by": PARTITION_BY_TS,
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        server_default=sql_text("uuidv7()"), comment="Идентификатор (UUIDv7)"
    )
    account_id: Mapped[uuid.UUID] = account_fk()
    ts: Mapped[datetime] = mapped_column(
        server_default=sql_text("now()"), comment="Когда вызвано (UTC); ключ партиции"
    )
    endpoint: Mapped[str] = mapped_column(comment="Эндпоинт, например GET /2/users/:id/tweets")
    resources: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Сколько ресурсов (постов, профилей) оплачено"
    )
    cost_micro_usd: Mapped[int] = mapped_column(
        BigInteger, server_default=sql_text("0"), comment="Цена вызова, микродоллары"
    )
    utc_day: Mapped[dt.date] = mapped_column(comment="День по UTC (для дневного лимита X)")
    http_status: Mapped[int | None] = mapped_column(comment="HTTP-код ответа")
    status: Mapped[str] = mapped_column(comment="ok / error")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("account_id", "scope", "period"),
        one_of("scope", BUDGET_SCOPES),
        one_of("period", BUDGET_PERIODS),
        {"comment": "Лимиты расходов"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    scope: Mapped[str] = mapped_column(comment="На что: llm / x_api / x_lookup / sniper")
    period: Mapped[str] = mapped_column(comment="За какой период: day / month")
    limit_micro_usd: Mapped[int] = mapped_column(BigInteger, comment="Лимит, микродоллары")
    hard: Mapped[bool] = mapped_column(
        server_default=sql_text("true"), comment="Жёсткий: при превышении вызовы блокируются"
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key"),
        Index("ix_jobs_status_run_at", "status", "run_at"),
        one_of("status", JOB_STATUSES),
        {"comment": "Очередь фоновых задач (claim через FOR UPDATE SKIP LOCKED)"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(comment="Тип задачи, например x.sync_own_posts")
    payload: Mapped[dict[str, Any]] = jsonb("Параметры задачи")
    run_at: Mapped[datetime] = mapped_column(
        server_default=sql_text("now()"), comment="Не раньше этого момента (UTC)"
    )
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'queued'"), comment="queued / running / done / dead"
    )
    attempts: Mapped[int] = mapped_column(server_default=sql_text("0"), comment="Сделано попыток")
    max_attempts: Mapped[int] = mapped_column(
        server_default=sql_text("5"), comment="После стольких неудач — dead"
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        comment="Ключ идемпотентности; повтор не создаёт новую задачу"
    )
    locked_by: Mapped[str | None] = mapped_column(comment="Воркер, взявший задачу")
    locked_at: Mapped[datetime | None] = mapped_column(comment="Когда взята (UTC)")
    last_error: Mapped[str | None] = mapped_column(comment="Последняя ошибка (без секретов)")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    finished_at: Mapped[datetime | None] = mapped_column(comment="Когда завершена (UTC)")


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("account_id", "kind"),
        {"comment": "Расписания периодических задач по поясу аккаунта"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(comment="Тип задачи, которую ставит расписание")
    at_local_time: Mapped[dt.time] = mapped_column(comment="Время запуска по поясу аккаунта")
    weekday: Mapped[int | None] = mapped_column(
        SmallInteger, comment="День недели 0=пн..6=вс; NULL — каждый день"
    )
    enabled: Mapped[bool] = mapped_column(server_default=sql_text("true"), comment="Включено")
    payload: Mapped[dict[str, Any]] = jsonb("Параметры задачи")
    last_run_at: Mapped[datetime | None] = mapped_column(comment="Последний запуск (UTC)")
    created_at: Mapped[datetime] = created_at()


class BotSession(Base):
    __tablename__ = "bot_sessions"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id"),
        {"comment": "Состояние диалогов бота (FSM) пользователя в аккаунте"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk(
        "Аккаунт диалога; до выбора аккаунта — системный global"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), comment="Пользователь")
    state: Mapped[str | None] = mapped_column(comment="Состояние FSM")
    data: Mapped[dict[str, Any]] = jsonb("Данные FSM")
    updated_at: Mapped[datetime] = updated_at()
