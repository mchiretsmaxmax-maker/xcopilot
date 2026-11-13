# ruff: noqa: E501
"""Начальная схема MVP: расширения, все таблицы раздела 0.4 (кроме A/B), индексы,
CHECK на статусы, комментарии, партиции DEFAULT журналов, представления метрик.

Revision ID: 0001
Revises:
Create Date: 2026-11-13 12:33:34.180037
"""

from collections.abc import Sequence

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PARTITIONED = ("events", "post_metric_snapshots", "llm_calls", "x_api_calls")

V_POST_CHECKPOINTS = """
CREATE VIEW v_post_checkpoints AS
SELECT DISTINCT ON (s.account_id, s.x_post_id, c.checkpoint)
       s.account_id, s.x_post_id, c.checkpoint, c.minutes AS checkpoint_minutes,
       s.ts, s.age_minutes, s.impressions, s.likes, s.replies, s.reposts, s.quotes,
       s.bookmarks, s.url_clicks, s.profile_clicks
FROM post_metric_snapshots s
JOIN (VALUES ('1h', 60), ('24h', 1440), ('72h', 4320), ('7d', 10080))
     AS c(checkpoint, minutes) ON s.age_minutes >= c.minutes
ORDER BY s.account_id, s.x_post_id, c.checkpoint, s.age_minutes
"""

V_DRAFT_FUNNEL = """
CREATE VIEW v_draft_funnel AS
SELECT d.account_id,
       (d.created_at AT TIME ZONE 'UTC')::date AS day,
       count(*) AS generated,
       count(*) FILTER (WHERE d.status IN ('shown', 'approved', 'edited', 'rejected',
                                          'published', 'unconfirmed')) AS shown,
       count(*) FILTER (WHERE d.status IN ('approved', 'edited', 'published',
                                          'unconfirmed')) AS approved,
       count(p.id) AS published
FROM drafts d
LEFT JOIN publications p ON p.draft_id = d.id AND p.account_id = d.account_id
GROUP BY d.account_id, (d.created_at AT TIME ZONE 'UTC')::date
"""

VIEW_COMMENTS = {
    "v_post_checkpoints": (
        "Метрики поста на отметках 1ч / 24ч / 72ч / 7д (первый снимок не раньше отметки)",
        {
            "account_id": "Аккаунт-владелец (тенант)",
            "x_post_id": "ID поста в X",
            "checkpoint": "Отметка: 1h / 24h / 72h / 7d",
            "checkpoint_minutes": "Отметка в минутах",
            "ts": "Когда снят снимок (UTC)",
            "age_minutes": "Фактический возраст поста на снимке, минут",
            "impressions": "Показы",
            "likes": "Лайки",
            "replies": "Ответы",
            "reposts": "Репосты",
            "quotes": "Цитаты",
            "bookmarks": "Закладки",
            "url_clicks": "Клики по ссылке",
            "profile_clicks": "Клики по профилю",
        },
    ),
    "v_draft_funnel": (
        "Воронка черновиков по дням (UTC): сгенерирован → показан → одобрен → опубликован",
        {
            "account_id": "Аккаунт-владелец (тенант)",
            "day": "День создания черновика (UTC)",
            "generated": "Сгенерировано черновиков",
            "shown": "Показано владельцу",
            "approved": "Одобрено (с правками или без)",
            "published": "Опубликовано",
        },
    ),
}


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column(
            "kind",
            sa.String(),
            server_default=sa.text("'x'"),
            nullable=False,
            comment="Тип: x — аккаунт X, global — системный",
        ),
        sa.Column(
            "handle", sa.String(), nullable=False, comment="Имя в X без @ (у системного — global)"
        ),
        sa.Column("x_user_id", sa.String(), nullable=True, comment="Числовой ID пользователя в X"),
        sa.Column("niche", sa.String(), nullable=True, comment="Ниша аккаунта своими словами"),
        sa.Column(
            "lang",
            sa.String(),
            server_default=sa.text("'ru'"),
            nullable=False,
            comment="Основной язык постов",
        ),
        sa.Column(
            "tz",
            sa.String(),
            server_default=sa.text("'Europe/Minsk'"),
            nullable=False,
            comment="Часовой пояс IANA для отчётов и расписаний",
        ),
        sa.Column(
            "x_read_mode",
            sa.String(),
            server_default=sa.text("'owned'"),
            nullable=False,
            comment="Режим чтения X: off / owned / paid",
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Настройки аккаунта (схема pydantic)",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="Состояние: active / paused / archived",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.CheckConstraint("kind IN ('x', 'global')", name=op.f("ck_accounts_kind_allowed")),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')", name=op.f("ck_accounts_status_allowed")
        ),
        sa.CheckConstraint(
            "x_read_mode IN ('off', 'owned', 'paid')", name=op.f("ck_accounts_x_read_mode_allowed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("kind", "handle", name=op.f("uq_accounts_kind_handle")),
        comment="X-аккаунты (тенанты); глобальные данные принадлежат аккаунту kind=global",
    )
    op.create_table(
        "embedding_models",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("name", sa.String(), nullable=False, comment="Имя модели, например в fastembed"),
        sa.Column("dim", sa.Integer(), nullable=False, comment="Размерность вектора"),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Используется для новых векторов и поиска",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embedding_models")),
        sa.UniqueConstraint("name", name=op.f("uq_embedding_models_name")),
        comment="Реестр моделей эмбеддингов (глобальный)",
    )
    op.create_table(
        "metric_definitions",
        sa.Column("key", sa.String(), nullable=False, comment="Ключ метрики"),
        sa.Column("version", sa.Integer(), nullable=False, comment="Версия формулы"),
        sa.Column("title", sa.String(), nullable=False, comment="Название для людей"),
        sa.Column("description", sa.String(), nullable=True, comment="Что показывает"),
        sa.Column(
            "unit",
            sa.String(),
            nullable=False,
            comment="Единица: ratio / count / micro_usd / minutes",
        ),
        sa.Column("grain", sa.String(), nullable=False, comment="Зерно: day / week"),
        sa.Column(
            "dimensions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Измерения (ключи dims)",
        ),
        sa.Column(
            "formula_sql",
            sa.String(),
            nullable=False,
            comment="SQL формулы (исполняется READ ONLY)",
        ),
        sa.Column(
            "formula_hash",
            sa.String(),
            nullable=False,
            comment="Хеш формулы для контроля изменений",
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Считается ли метрика",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.CheckConstraint(
            "grain IN ('day', 'week')", name=op.f("ck_metric_definitions_grain_allowed")
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_metric_definitions")),
        comment="Каталог метрик; формулы грузятся из metrics/definitions/*.sql",
    )
    op.create_table(
        "prompts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("key", sa.String(), nullable=False, comment="Ключ промпта, например writer.post"),
        sa.Column("version", sa.Integer(), nullable=False, comment="Версия, с 1"),
        sa.Column(
            "role",
            sa.String(),
            nullable=False,
            comment="Роль модели: writer / judge / cheap и т. п.",
        ),
        sa.Column("template", sa.String(), nullable=False, comment="Текст шаблона"),
        sa.Column(
            "output_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="JSON-схема ожидаемого ответа",
        ),
        sa.Column("content_hash", sa.String(), nullable=False, comment="Хеш шаблона и схемы"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompts")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_prompts_key_version")),
        comment="Версии промптов (глобальные)",
    )
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column(
            "tg_user_id", sa.BigInteger(), nullable=False, comment="ID пользователя в Telegram"
        ),
        sa.Column("name", sa.String(), nullable=True, comment="Имя для обращения"),
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Администратор всего сервиса",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("tg_user_id", name=op.f("uq_users_tg_user_id")),
        comment="Люди в Telegram, которые работают с ботом",
    )
    op.create_table(
        "account_members",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="Пользователь"),
        sa.Column("role", sa.String(), nullable=False, comment="Роль: owner / editor / viewer"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')", name=op.f("ck_account_members_role_allowed")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_account_members_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_account_members_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_members")),
        sa.UniqueConstraint(
            "account_id", "user_id", name=op.f("uq_account_members_account_id_user_id")
        ),
        comment="Кто к какому аккаунту имеет доступ и с какой ролью",
    )
    op.create_index("ix_account_members_user_id", "account_members", ["user_id"], unique=False)
    op.create_table(
        "account_metric_snapshots",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("date", sa.Date(), nullable=False, comment="День по поясу аккаунта"),
        sa.Column("followers", sa.Integer(), nullable=True, comment="Подписчики"),
        sa.Column("following", sa.Integer(), nullable=True, comment="Подписки"),
        sa.Column("posts_count", sa.Integer(), nullable=True, comment="Всего постов"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_account_metric_snapshots_account_id_accounts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_metric_snapshots")),
        sa.UniqueConstraint(
            "account_id", "date", name=op.f("uq_account_metric_snapshots_account_id_date")
        ),
        comment="Метрики аккаунта X по дням",
    )
    op.create_table(
        "bot_sessions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column(
            "account_id",
            sa.Uuid(),
            nullable=False,
            comment="Аккаунт диалога; до выбора аккаунта — системный global",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="Пользователь"),
        sa.Column("state", sa.String(), nullable=True, comment="Состояние FSM"),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Данные FSM",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_bot_sessions_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_bot_sessions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_sessions")),
        sa.UniqueConstraint(
            "account_id", "user_id", name=op.f("uq_bot_sessions_account_id_user_id")
        ),
        comment="Состояние диалогов бота (FSM) пользователя в аккаунте",
    )
    op.create_table(
        "budgets",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("scope", sa.String(), nullable=False, comment="На что: llm / x_api / sniper"),
        sa.Column("period", sa.String(), nullable=False, comment="За какой период: day / month"),
        sa.Column(
            "limit_micro_usd", sa.BigInteger(), nullable=False, comment="Лимит, микродоллары"
        ),
        sa.Column(
            "hard",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Жёсткий: при превышении вызовы блокируются",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.CheckConstraint("period IN ('day', 'month')", name=op.f("ck_budgets_period_allowed")),
        sa.CheckConstraint(
            "scope IN ('llm', 'x_api', 'sniper')", name=op.f("ck_budgets_scope_allowed")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_budgets_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint(
            "account_id", "scope", "period", name=op.f("uq_budgets_account_id_scope_period")
        ),
        comment="Лимиты расходов",
    )
    op.create_table(
        "competitors",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("handle", sa.String(), nullable=False, comment="Имя в X без @"),
        sa.Column("x_user_id", sa.String(), nullable=True, comment="Числовой ID пользователя в X"),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Приоритет опроса (больше — чаще)",
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Следим ли сейчас",
        ),
        sa.Column("since_id", sa.String(), nullable=True, comment="Последний прочитанный ID поста"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_competitors_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_competitors")),
        sa.UniqueConstraint("account_id", "handle", name=op.f("uq_competitors_account_id_handle")),
        comment="Аккаунты X, за которыми следим",
    )
    op.create_table(
        "events",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда произошло (UTC); ключ партиции",
        ),
        sa.Column(
            "type", sa.String(), nullable=False, comment="Тип события, например review.approved"
        ),
        sa.Column("actor", sa.String(), nullable=False, comment="Кто: user / system / model"),
        sa.Column("user_id", sa.Uuid(), nullable=True, comment="Пользователь, если actor = user"),
        sa.Column(
            "entity_type", sa.String(), nullable=True, comment="Тип сущности, например draft"
        ),
        sa.Column("entity_id", sa.Uuid(), nullable=True, comment="ID сущности"),
        sa.Column(
            "props",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Свойства события",
        ),
        sa.CheckConstraint(
            "actor IN ('user', 'system', 'model')", name=op.f("ck_events_actor_allowed")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_events_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("ts", "id", name=op.f("pk_events")),
        comment="Журнал всех событий продукта; только добавление, партиции по месяцам",
        postgresql_partition_by="RANGE (ts)",
    )
    op.create_index(
        "ix_events_account_id_entity",
        "events",
        ["account_id", "entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_events_account_id_type_ts", "events", ["account_id", "type", "ts"], unique=False
    )
    op.create_table(
        "facts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("statement", sa.String(), nullable=False, comment="Утверждение"),
        sa.Column("value", sa.String(), nullable=True, comment="Значение (число или текст)"),
        sa.Column("unit", sa.String(), nullable=True, comment="Единица измерения"),
        sa.Column("source_url", sa.String(), nullable=False, comment="Где проверено"),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Когда факт был верен (UTC)",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="После этого момента не использовать",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_facts_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facts")),
        comment="Проверяемые факты с источником и сроком годности",
    )
    op.create_index(
        "ix_facts_account_id_expires_at", "facts", ["account_id", "expires_at"], unique=False
    )
    op.create_table(
        "insights",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "kind",
            sa.String(),
            nullable=False,
            comment="Тип вывода, например best_time или format_lift",
        ),
        sa.Column(
            "body",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Содержимое вывода (схема pydantic)",
        ),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Ссылки на данные, из которых сделан вывод",
        ),
        sa.Column("confidence", sa.Double(), nullable=True, comment="Уверенность 0..1"),
        sa.Column(
            "valid_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="До какого момента актуален",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_insights_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insights")),
        comment="Выводы аналитики со ссылками на данные",
    )
    op.create_index(
        "ix_insights_account_id_kind_created_at",
        "insights",
        ["account_id", "kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "kind", sa.String(), nullable=False, comment="Тип задачи, например x.sync_own_posts"
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Параметры задачи",
        ),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Не раньше этого момента (UTC)",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'queued'"),
            nullable=False,
            comment="queued / running / done / dead",
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Сделано попыток",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
            comment="После стольких неудач — dead",
        ),
        sa.Column(
            "idempotency_key",
            sa.String(),
            nullable=True,
            comment="Ключ идемпотентности; повтор не создаёт новую задачу",
        ),
        sa.Column("locked_by", sa.String(), nullable=True, comment="Воркер, взявший задачу"),
        sa.Column(
            "locked_at", sa.DateTime(timezone=True), nullable=True, comment="Когда взята (UTC)"
        ),
        sa.Column(
            "last_error", sa.String(), nullable=True, comment="Последняя ошибка (без секретов)"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда завершена (UTC)",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'dead')", name=op.f("ck_jobs_status_allowed")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_jobs_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint(
            "account_id", "idempotency_key", name=op.f("uq_jobs_account_id_idempotency_key")
        ),
        comment="Очередь фоновых задач (claim через FOR UPDATE SKIP LOCKED)",
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"], unique=False)
    op.create_table(
        "lessons",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "kind",
            sa.String(),
            nullable=False,
            comment="do — делать, dont — не делать, pattern — приём",
        ),
        sa.Column("text", sa.String(), nullable=False, comment="Формулировка урока"),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'proposed'"),
            nullable=False,
            comment="proposed / accepted / rejected / retired",
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="ID ревью, из которых выведен урок",
        ),
        sa.Column(
            "weight",
            sa.Double(),
            server_default=sa.text("1.0"),
            nullable=False,
            comment="Вес урока при сборке инструкций",
        ),
        sa.Column("decided_by", sa.Uuid(), nullable=True, comment="Кто принял решение по уроку"),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда принято решение (UTC)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "kind IN ('do', 'dont', 'pattern')", name=op.f("ck_lessons_kind_allowed")
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'rejected', 'retired')",
            name=op.f("ck_lessons_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_lessons_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_lessons_decided_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
        comment="Правила из правок и отказов владельца",
    )
    op.create_index(
        "ix_lessons_account_id_status", "lessons", ["account_id", "status"], unique=False
    )
    op.create_table(
        "llm_calls",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда вызвано (UTC); ключ партиции",
        ),
        sa.Column(
            "role", sa.String(), nullable=False, comment="Роль: writer / judge / cheap / embed"
        ),
        sa.Column(
            "provider", sa.String(), nullable=False, comment="Провайдер: anthropic / deepseek / jev"
        ),
        sa.Column("model", sa.String(), nullable=False, comment="Модель у провайдера"),
        sa.Column("prompt_id", sa.Uuid(), nullable=True, comment="Версия промпта"),
        sa.Column(
            "purpose", sa.String(), nullable=False, comment="Зачем вызвали, например draft.generate"
        ),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Токены входа",
        ),
        sa.Column(
            "cached_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Токены входа из кеша",
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Токены ответа",
        ),
        sa.Column(
            "cost_micro_usd",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Цена вызова, микродоллары",
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True, comment="Длительность, мс"),
        sa.Column("status", sa.String(), nullable=False, comment="ok / error"),
        sa.Column("error", sa.String(), nullable=True, comment="Класс ошибки (без секретов)"),
        sa.CheckConstraint("status IN ('ok', 'error')", name=op.f("ck_llm_calls_status_allowed")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_llm_calls_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompts.id"], name=op.f("fk_llm_calls_prompt_id_prompts")
        ),
        sa.PrimaryKeyConstraint("ts", "id", name=op.f("pk_llm_calls")),
        comment="Журнал вызовов моделей; только добавление, партиции по месяцам",
        postgresql_partition_by="RANGE (ts)",
    )
    op.create_index(
        "ix_llm_calls_account_id_role_ts", "llm_calls", ["account_id", "role", "ts"], unique=False
    )
    op.create_table(
        "metric_values",
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("metric_key", sa.String(), nullable=False, comment="Метрика"),
        sa.Column("grain", sa.String(), nullable=False, comment="day / week"),
        sa.Column(
            "period_start", sa.Date(), nullable=False, comment="Начало периода по поясу аккаунта"
        ),
        sa.Column(
            "dims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Значения измерений",
        ),
        sa.Column("dims_hash", sa.String(), nullable=False, comment="Хеш dims для ключа"),
        sa.Column("value", sa.Numeric(), nullable=True, comment="Значение метрики"),
        sa.Column("sample_size", sa.Integer(), nullable=False, comment="Размер выборки"),
        sa.Column(
            "definition_version",
            sa.Integer(),
            nullable=False,
            comment="Версия формулы, которой посчитано",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Когда посчитано (UTC)",
        ),
        sa.CheckConstraint("grain IN ('day', 'week')", name=op.f("ck_metric_values_grain_allowed")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_metric_values_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["metric_key"],
            ["metric_definitions.key"],
            name=op.f("fk_metric_values_metric_key_metric_definitions"),
        ),
        sa.PrimaryKeyConstraint(
            "account_id",
            "metric_key",
            "grain",
            "period_start",
            "dims_hash",
            name=op.f("pk_metric_values"),
        ),
        comment="Суточные и недельные свёртки метрик",
    )
    op.create_table(
        "post_metric_snapshots",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("x_post_id", sa.String(), nullable=False, comment="ID поста в X"),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда снят снимок (UTC); ключ партиции",
        ),
        sa.Column(
            "age_minutes",
            sa.Integer(),
            nullable=False,
            comment="Возраст поста на момент снимка, минут",
        ),
        sa.Column("impressions", sa.BigInteger(), nullable=True, comment="Показы"),
        sa.Column("likes", sa.Integer(), nullable=True, comment="Лайки"),
        sa.Column("replies", sa.Integer(), nullable=True, comment="Ответы"),
        sa.Column("reposts", sa.Integer(), nullable=True, comment="Репосты"),
        sa.Column("quotes", sa.Integer(), nullable=True, comment="Цитаты"),
        sa.Column("bookmarks", sa.Integer(), nullable=True, comment="Закладки"),
        sa.Column("url_clicks", sa.Integer(), nullable=True, comment="Клики по ссылке"),
        sa.Column("profile_clicks", sa.Integer(), nullable=True, comment="Клики по профилю"),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Ответ API как есть",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_post_metric_snapshots_account_id_accounts"),
        ),
        sa.PrimaryKeyConstraint("ts", "id", name=op.f("pk_post_metric_snapshots")),
        comment="Временной ряд метрик поста X; только добавление, партиции по месяцам",
        postgresql_partition_by="RANGE (ts)",
    )
    op.create_index(
        "ix_post_metric_snapshots_account_id_post_ts",
        "post_metric_snapshots",
        ["account_id", "x_post_id", "ts"],
        unique=False,
    )
    op.create_table(
        "schedules",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "kind", sa.String(), nullable=False, comment="Тип задачи, которую ставит расписание"
        ),
        sa.Column(
            "at_local_time", sa.Time(), nullable=False, comment="Время запуска по поясу аккаунта"
        ),
        sa.Column(
            "weekday",
            sa.SmallInteger(),
            nullable=True,
            comment="День недели 0=пн..6=вс; NULL — каждый день",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Включено",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Параметры задачи",
        ),
        sa.Column(
            "last_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Последний запуск (UTC)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_schedules_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
        sa.UniqueConstraint("account_id", "kind", name=op.f("uq_schedules_account_id_kind")),
        comment="Расписания периодических задач по поясу аккаунта",
    )
    op.create_table(
        "secrets",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("kind", sa.String(), nullable=False, comment="Что за секрет, например x_oauth"),
        sa.Column(
            "ciphertext",
            sa.LargeBinary(),
            nullable=False,
            comment="Шифротекст Fernet; открытый текст в БД не хранится",
        ),
        sa.Column(
            "key_version",
            sa.Integer(),
            nullable=False,
            comment="Версия ключа SECRETS_KEY для ротации",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_secrets_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_secrets")),
        sa.UniqueConstraint("account_id", "kind", name=op.f("uq_secrets_account_id_kind")),
        comment="Зашифрованные (Fernet) токены и ключи аккаунта",
    )
    op.create_table(
        "sources",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "kind",
            sa.String(),
            nullable=False,
            comment="Что это: own_post / liked_sample / competitor_post / file / chat / note",
        ),
        sa.Column(
            "origin", sa.String(), nullable=False, comment="Откуда: manual / x_api / oembed / radar"
        ),
        sa.Column(
            "external_id", sa.String(), nullable=True, comment="ID во внешней системе (пост X)"
        ),
        sa.Column("url", sa.String(), nullable=True, comment="Ссылка на оригинал"),
        sa.Column("author_handle", sa.String(), nullable=True, comment="Автор в X без @"),
        sa.Column(
            "content_hash",
            sa.String(),
            nullable=False,
            comment="Хеш нормализованного содержимого; уникален в аккаунте (дедуп ингеста)",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'new'"),
            nullable=False,
            comment="Обработка: new / parsed / indexed / failed / excluded",
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Прочие поля источника (схема pydantic)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "kind IN ('own_post', 'liked_sample', 'competitor_post', 'file', 'chat', 'note')",
            name=op.f("ck_sources_kind_allowed"),
        ),
        sa.CheckConstraint(
            "origin IN ('manual', 'x_api', 'oembed', 'radar')",
            name=op.f("ck_sources_origin_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('new', 'parsed', 'indexed', 'failed', 'excluded')",
            name=op.f("ck_sources_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_sources_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint(
            "account_id", "content_hash", name=op.f("uq_sources_account_id_content_hash")
        ),
        comment="Всё, что пришло в систему: посты, образцы, файлы, заметки",
    )
    op.create_index(
        "ix_sources_account_id_kind_created_at",
        "sources",
        ["account_id", "kind", "created_at"],
        unique=False,
    )
    op.create_table(
        "style_passports",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("version", sa.Integer(), nullable=False, comment="Номер версии в аккаунте, с 1"),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'draft'"),
            nullable=False,
            comment="draft / active / archived",
        ),
        sa.Column(
            "body",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Паспорт: тон, приёмы, лексика (схема pydantic)",
        ),
        sa.Column(
            "hard_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Жёсткие правила, проверяемые кодом",
        ),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="ID источников, на которых построен паспорт",
        ),
        sa.Column("created_by", sa.String(), nullable=False, comment="Кто создал: llm / user"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "created_by IN ('llm', 'user')", name=op.f("ck_style_passports_created_by_allowed")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name=op.f("ck_style_passports_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_style_passports_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_style_passports")),
        sa.UniqueConstraint(
            "account_id", "version", name=op.f("uq_style_passports_account_id_version")
        ),
        comment="Паспорт голоса аккаунта; версии, активна ровно одна",
    )
    op.create_index(
        "uq_style_passports_one_active",
        "style_passports",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "topics",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("title", sa.String(), nullable=False, comment="Тема одной строкой"),
        sa.Column("angle", sa.String(), nullable=True, comment="Угол подачи"),
        sa.Column(
            "origin",
            sa.String(),
            nullable=False,
            comment="Откуда: suggested / manual / radar / plan / trend_tail",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'new'"),
            nullable=False,
            comment="new / planned / in_progress / used / dropped",
        ),
        sa.Column(
            "planned_for",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="На когда запланирована (UTC)",
        ),
        sa.Column(
            "dedupe_vec",
            pgvector.sqlalchemy.vector.VECTOR(dim=384),
            nullable=True,
            comment="Вектор темы для поиска повторов",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "origin IN ('suggested', 'manual', 'radar', 'plan', 'trend_tail')",
            name=op.f("ck_topics_origin_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('new', 'planned', 'in_progress', 'used', 'dropped')",
            name=op.f("ck_topics_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_topics_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topics")),
        comment="Темы для постов",
    )
    op.create_index(
        "ix_topics_account_id_status_planned_for",
        "topics",
        ["account_id", "status", "planned_for"],
        unique=False,
    )
    op.create_table(
        "x_api_calls",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда вызвано (UTC); ключ партиции",
        ),
        sa.Column(
            "endpoint",
            sa.String(),
            nullable=False,
            comment="Эндпоинт, например GET /2/users/:id/tweets",
        ),
        sa.Column(
            "resources",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Сколько ресурсов (постов, профилей) оплачено",
        ),
        sa.Column(
            "cost_micro_usd",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Цена вызова, микродоллары",
        ),
        sa.Column(
            "utc_day", sa.Date(), nullable=False, comment="День по UTC (для дневного лимита X)"
        ),
        sa.Column("http_status", sa.Integer(), nullable=True, comment="HTTP-код ответа"),
        sa.Column("status", sa.String(), nullable=False, comment="ok / error"),
        sa.CheckConstraint("status IN ('ok', 'error')", name=op.f("ck_x_api_calls_status_allowed")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_x_api_calls_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("ts", "id", name=op.f("pk_x_api_calls")),
        comment="Журнал вызовов X API; только добавление, партиции по месяцам",
        postgresql_partition_by="RANGE (ts)",
    )
    op.create_index(
        "ix_x_api_calls_account_id_utc_day", "x_api_calls", ["account_id", "utc_day"], unique=False
    )
    op.create_table(
        "documents",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("source_id", sa.Uuid(), nullable=False, comment="Источник текста"),
        sa.Column("text", sa.String(), nullable=False, comment="Исходный извлечённый текст"),
        sa.Column("text_clean", sa.String(), nullable=True, comment="Текст после очистки"),
        sa.Column(
            "token_count", sa.Integer(), nullable=True, comment="Число токенов в очищенном тексте"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_documents_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_documents_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        comment="Текст, извлечённый из источника",
    )
    op.create_index(
        "ix_documents_account_id_source_id", "documents", ["account_id", "source_id"], unique=False
    )
    op.create_table(
        "generation_runs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("topic_id", sa.Uuid(), nullable=True, comment="Тема (у ответов может не быть)"),
        sa.Column(
            "kind",
            sa.String(),
            nullable=False,
            comment="post / thread / reply / quote / trend_tail",
        ),
        sa.Column(
            "context_pack",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="ID кусков и уроков, попавших в контекст",
        ),
        sa.Column("passport_version", sa.Integer(), nullable=True, comment="Версия паспорта стиля"),
        sa.Column("prompt_id", sa.Uuid(), nullable=True, comment="Версия промпта писателя"),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'running'"),
            nullable=False,
            comment="running / done / failed",
        ),
        sa.Column("error", sa.String(), nullable=True, comment="Текст ошибки (без секретов)"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True, comment="Когда закончен (UTC)"
        ),
        sa.CheckConstraint(
            "kind IN ('post', 'thread', 'reply', 'quote', 'trend_tail')",
            name=op.f("ck_generation_runs_kind_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')",
            name=op.f("ck_generation_runs_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_generation_runs_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompts.id"], name=op.f("fk_generation_runs_prompt_id_prompts")
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name=op.f("fk_generation_runs_topic_id_topics")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_runs")),
        comment="Один запуск генерации черновиков",
    )
    op.create_index(
        "ix_generation_runs_account_id_created_at",
        "generation_runs",
        ["account_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "chunks",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("document_id", sa.Uuid(), nullable=False, comment="Документ, из которого кусок"),
        sa.Column(
            "idx", sa.Integer(), nullable=False, comment="Порядковый номер куска в документе, с 0"
        ),
        sa.Column("text", sa.String(), nullable=False, comment="Текст куска"),
        sa.Column(
            "tags",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Метки (тема, формат, приём)",
        ),
        sa.Column("lang", sa.String(), nullable=True, comment="Язык куска"),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, text)", persisted=True),
            nullable=False,
            comment="Полнотекстовый вектор (генерируется из text)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_chunks_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name=op.f("fk_chunks_document_id_documents")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "account_id", "document_id", "idx", name=op.f("uq_chunks_account_id_document_id_idx")
        ),
        comment="Куски документов для поиска (полнотекстового, триграммного, векторного)",
    )
    op.create_index(
        "ix_chunks_text_trgm",
        "chunks",
        ["text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin")
    op.create_table(
        "drafts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("run_id", sa.Uuid(), nullable=False, comment="Запуск генерации"),
        sa.Column(
            "variant_no",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Номер варианта в запуске, с 1",
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Номер ревизии варианта, с 1",
        ),
        sa.Column("parent_draft_id", sa.Uuid(), nullable=True, comment="Предыдущая ревизия"),
        sa.Column(
            "format",
            sa.String(),
            nullable=False,
            comment="Формат: post / thread / reply / quote и т. п.",
        ),
        sa.Column("text", sa.String(), nullable=False, comment="Текст черновика"),
        sa.Column(
            "parts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Части треда по порядку",
        ),
        sa.Column("reply_to_url", sa.String(), nullable=True, comment="Пост, на который отвечаем"),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'generated'"),
            nullable=False,
            comment="Статус по машине DraftStatus",
        ),
        sa.Column(
            "lint_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Отчёт линтера",
        ),
        sa.Column(
            "judge_verdict",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Вердикт судьи",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда изменено (UTC)",
        ),
        sa.CheckConstraint(
            "status IN ('generated', 'linted', 'judged', 'shown', 'approved', 'edited', 'rejected', 'published', 'unconfirmed', 'needs_owner', 'expired')",
            name=op.f("ck_drafts_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_drafts_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["parent_draft_id"], ["drafts.id"], name=op.f("fk_drafts_parent_draft_id_drafts")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["generation_runs.id"], name=op.f("fk_drafts_run_id_generation_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drafts")),
        sa.UniqueConstraint(
            "account_id",
            "run_id",
            "variant_no",
            "revision",
            name=op.f("uq_drafts_account_id_run_id_variant_no_revision"),
        ),
        comment="Черновики и их ревизии (ревизия ссылается на родителя)",
    )
    op.create_index(
        "ix_drafts_account_id_parent_draft_id",
        "drafts",
        ["account_id", "parent_draft_id"],
        unique=False,
    )
    op.create_index(
        "ix_drafts_account_id_status_created_at",
        "drafts",
        ["account_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "embeddings",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("chunk_id", sa.Uuid(), nullable=False, comment="Кусок"),
        sa.Column("model_id", sa.Uuid(), nullable=False, comment="Модель, которой посчитан вектор"),
        sa.Column(
            "vec",
            pgvector.sqlalchemy.vector.VECTOR(dim=384),
            nullable=False,
            comment="Вектор (384 измерения)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_embeddings_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.id"], name=op.f("fk_embeddings_chunk_id_chunks")
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["embedding_models.id"],
            name=op.f("fk_embeddings_model_id_embedding_models"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_embeddings")),
        sa.UniqueConstraint(
            "account_id",
            "chunk_id",
            "model_id",
            name=op.f("uq_embeddings_account_id_chunk_id_model_id"),
        ),
        comment="Векторы кусков (pgvector, HNSW по косинусу)",
    )
    op.create_index(
        "ix_embeddings_vec_hnsw",
        "embeddings",
        ["vec"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vec": "vector_cosine_ops"},
    )
    op.create_table(
        "publications",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("draft_id", sa.Uuid(), nullable=False, comment="Черновик"),
        sa.Column("x_post_id", sa.String(), nullable=False, comment="ID поста в X"),
        sa.Column("url", sa.String(), nullable=False, comment="Ссылка на пост"),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Когда опубликован (UTC)",
        ),
        sa.Column(
            "matched_by",
            sa.String(),
            nullable=False,
            comment="Как связали: user_link / auto / manual",
        ),
        sa.Column(
            "match_score", sa.Double(), nullable=True, comment="Похожесть текста при автосверке"
        ),
        sa.Column("final_text", sa.String(), nullable=False, comment="Текст, который вышел в X"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "matched_by IN ('user_link', 'auto', 'manual')",
            name=op.f("ck_publications_matched_by_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_publications_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name=op.f("fk_publications_draft_id_drafts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publications")),
        sa.UniqueConstraint(
            "account_id", "draft_id", name=op.f("uq_publications_account_id_draft_id")
        ),
        sa.UniqueConstraint(
            "account_id", "x_post_id", name=op.f("uq_publications_account_id_x_post_id")
        ),
        comment="Связь черновика с опубликованным постом X",
    )
    op.create_table(
        "reviews",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Аккаунт-владелец (тенант)"),
        sa.Column("draft_id", sa.Uuid(), nullable=False, comment="Черновик"),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="Кто решил"),
        sa.Column(
            "action", sa.String(), nullable=False, comment="approve / edit / regenerate / reject"
        ),
        sa.Column(
            "reason_code", sa.String(), nullable=True, comment="Код причины отказа или правки"
        ),
        sa.Column("text_before", sa.String(), nullable=False, comment="Текст до решения"),
        sa.Column("text_after", sa.String(), nullable=True, comment="Текст после правки"),
        sa.Column(
            "edit_distance",
            sa.Double(),
            nullable=True,
            comment="Нормированная дистанция до/после, 0..1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "action IN ('approve', 'edit', 'regenerate', 'reject')",
            name=op.f("ck_reviews_action_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_reviews_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name=op.f("fk_reviews_draft_id_drafts")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_reviews_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        comment="Решения владельца по черновикам; пары «до/после»",
    )
    op.create_index(
        "ix_reviews_account_id_draft_id", "reviews", ["account_id", "draft_id"], unique=False
    )
    for table in PARTITIONED:
        op.execute(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT")
        op.execute(
            f"COMMENT ON TABLE {table}_default IS "
            + _q(f"Страховочная партиция {table}: строки вне месячных партиций")
        )
    op.execute(V_POST_CHECKPOINTS)
    op.execute(V_DRAFT_FUNNEL)
    for view, (comment, columns) in VIEW_COMMENTS.items():
        op.execute(f"COMMENT ON VIEW {view} IS {_q(comment)}")
        for column, text in columns.items():
            op.execute(f"COMMENT ON COLUMN {view}.{column} IS {_q(text)}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_draft_funnel")
    op.execute("DROP VIEW IF EXISTS v_post_checkpoints")
    op.drop_index("ix_reviews_account_id_draft_id", table_name="reviews")
    op.drop_table("reviews")
    op.drop_table("publications")
    op.drop_index(
        "ix_embeddings_vec_hnsw",
        table_name="embeddings",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"vec": "vector_cosine_ops"},
    )
    op.drop_table("embeddings")
    op.drop_index("ix_drafts_account_id_status_created_at", table_name="drafts")
    op.drop_index("ix_drafts_account_id_parent_draft_id", table_name="drafts")
    op.drop_table("drafts")
    op.drop_index("ix_chunks_tsv", table_name="chunks", postgresql_using="gin")
    op.drop_index(
        "ix_chunks_text_trgm",
        table_name="chunks",
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.drop_table("chunks")
    op.drop_index("ix_generation_runs_account_id_created_at", table_name="generation_runs")
    op.drop_table("generation_runs")
    op.drop_index("ix_documents_account_id_source_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_x_api_calls_account_id_utc_day", table_name="x_api_calls")
    op.drop_table("x_api_calls")
    op.drop_index("ix_topics_account_id_status_planned_for", table_name="topics")
    op.drop_table("topics")
    op.drop_index(
        "uq_style_passports_one_active",
        table_name="style_passports",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("style_passports")
    op.drop_index("ix_sources_account_id_kind_created_at", table_name="sources")
    op.drop_table("sources")
    op.drop_table("secrets")
    op.drop_table("schedules")
    op.drop_index("ix_post_metric_snapshots_account_id_post_ts", table_name="post_metric_snapshots")
    op.drop_table("post_metric_snapshots")
    op.drop_table("metric_values")
    op.drop_index("ix_llm_calls_account_id_role_ts", table_name="llm_calls")
    op.drop_table("llm_calls")
    op.drop_index("ix_lessons_account_id_status", table_name="lessons")
    op.drop_table("lessons")
    op.drop_index("ix_jobs_status_run_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_insights_account_id_kind_created_at", table_name="insights")
    op.drop_table("insights")
    op.drop_index("ix_facts_account_id_expires_at", table_name="facts")
    op.drop_table("facts")
    op.drop_index("ix_events_account_id_type_ts", table_name="events")
    op.drop_index("ix_events_account_id_entity", table_name="events")
    op.drop_table("events")
    op.drop_table("competitors")
    op.drop_table("budgets")
    op.drop_table("bot_sessions")
    op.drop_table("account_metric_snapshots")
    op.drop_index("ix_account_members_user_id", table_name="account_members")
    op.drop_table("account_members")
    op.drop_table("users")
    op.drop_table("prompts")
    op.drop_table("metric_definitions")
    op.drop_table("embedding_models")
    op.drop_table("accounts")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
