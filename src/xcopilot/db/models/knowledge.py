"""Знания (сырьё): источники, тексты, куски, векторы, конкуренты. Сырьё не изменяется."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import TSVECTOR
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

EMBEDDING_DIM = 384
# ТЗ v3.1: вид материала и отношение владельца — два независимых поля.
SOURCE_KINDS = ("post", "thread", "reply", "quote", "repost", "article", "note", "file", "chat")
SOURCE_STANCES = ("own", "inspiration", "competitor", "anti_example", "reference")
SOURCE_ORIGINS = (
    "manual_text",
    "link_oembed",
    "x_api",
    "screenshot",
    "tg_forward",
    "file",
    "url_fetch",
    "history_backfill",
    "radar",
)
SOURCE_STATUSES = ("new", "parsed", "indexed", "failed", "excluded")
ANALYSIS_STATUSES = ("pending", "analyzed", "skipped", "failed", "quarantined")
FOLLOWERS_ORIGINS = ("x_api", "screenshot", "manual")


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (
        UniqueConstraint("account_id", "handle"),
        UniqueConstraint("account_id", "id"),
        Index("uq_authors_self", "account_id", unique=True, postgresql_where=sql_text("is_self")),
        CheckConstraint("handle = lower(handle) AND handle !~ '^@'", name="handle_normalized"),
        CheckConstraint("followers >= 0", name="followers_nonneg"),
        CheckConstraint("trust IN (-1, 0, 1)", name="trust_allowed"),
        one_of("followers_origin", FOLLOWERS_ORIGINS),
        {
            "comment": "Авторы материалов. Нужны для нормировки охвата, независимости"
            " доказательств и блокировки источников"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    handle: Mapped[str] = mapped_column(comment="Хэндл в X без @, в нижнем регистре")
    x_user_id: Mapped[str | None] = mapped_column(comment="ID пользователя X, если известен")
    display_name: Mapped[str | None] = mapped_column(comment="Отображаемое имя")
    is_self: Mapped[bool] = mapped_column(
        server_default=sql_text("false"),
        comment="Это сам владелец аккаунта. Одна такая строка на аккаунт",
    )
    followers: Mapped[int | None] = mapped_column(
        comment="Число подписчиков на момент последнего наблюдения"
    )
    followers_observed_at: Mapped[datetime | None] = mapped_column(
        comment="Когда наблюдали число подписчиков"
    )
    followers_origin: Mapped[str | None] = mapped_column(
        comment="Откуда число: x_api, screenshot, manual"
    )
    niche: Mapped[str | None] = mapped_column(comment="Ниша автора (ключ таксономии niche)")
    trust: Mapped[int] = mapped_column(
        SmallInteger,
        server_default=sql_text("0"),
        comment="-1 — заблокирован (материалы не учатся), 0 — обычный, 1 — доверенный",
    )
    note: Mapped[str | None] = mapped_column(comment="Заметка владельца")
    created_at: Mapped[datetime] = created_at("Создано")
    updated_at: Mapped[datetime] = updated_at("Изменено")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("account_id", "content_hash"),
        UniqueConstraint("account_id", "id"),
        Index("ix_sources_account_id_kind_created_at", "account_id", "kind", "created_at"),
        Index(
            "ix_sources_learning",
            "account_id",
            "stance",
            "kind",
            "analysis_status",
            postgresql_where=sql_text("removed_at IS NULL"),
        ),
        Index("ix_sources_author", "account_id", "author_id", sql_text("posted_at DESC")),
        Index(
            "ix_sources_parent",
            "account_id",
            "parent_source_id",
            postgresql_where=sql_text("parent_source_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["account_id", "author_id"],
            ["authors.account_id", "authors.id"],
            name="fk_sources_author",
        ),
        ForeignKeyConstraint(
            ["account_id", "parent_source_id"],
            ["sources.account_id", "sources.id"],
            name="fk_sources_parent",
        ),
        ForeignKeyConstraint(
            ["account_id", "thread_root_id"],
            ["sources.account_id", "sources.id"],
            name="fk_sources_thread",
        ),
        one_of("kind", SOURCE_KINDS),
        one_of("stance", SOURCE_STANCES),
        one_of("origin", SOURCE_ORIGINS),
        one_of("status", SOURCE_STATUSES),
        one_of("analysis_status", ANALYSIS_STATUSES),
        CheckConstraint(
            "(analysis_status = 'quarantined') = (quarantine_reason IS NOT NULL)",
            name="quarantine_reason",
        ),
        {"comment": "Всё, что пришло в систему: посты, образцы, файлы, заметки"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(
        comment="Что это физически: post, thread, reply, quote, repost, article, note, file, chat"
    )
    origin: Mapped[str] = mapped_column(
        comment="Канал получения: manual_text, link_oembed, x_api, screenshot, tg_forward,"
        " file, url_fetch, history_backfill, radar"
    )
    external_id: Mapped[str | None] = mapped_column(comment="ID во внешней системе (пост X)")
    url: Mapped[str | None] = mapped_column(comment="Ссылка на оригинал")
    author_handle: Mapped[str | None] = mapped_column(comment="Автор в X без @")
    content_hash: Mapped[str] = mapped_column(
        comment="Хеш нормализованного содержимого; уникален в аккаунте (дедуп ингеста)"
    )
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'new'"),
        comment="Обработка: new / parsed / indexed / failed / excluded",
    )
    meta: Mapped[dict[str, Any]] = jsonb("Прочие поля источника (схема pydantic)")
    created_at: Mapped[datetime] = created_at()
    stance: Mapped[str] = mapped_column(
        comment="Отношение владельца: own — моё, inspiration — нравится, competitor — конкурент,"
        " anti_example — так не надо, reference — материал и факты"
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(comment="Автор материала")
    parent_source_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Пост, на который это ответ или цитата. Нужен как контекст разбора"
    )
    thread_root_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Первый пост треда, если материал — часть треда"
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        comment="Когда пост вышел в X (не когда попал в базу). От него считается свежесть"
        " доказательств"
    )
    lang: Mapped[str | None] = mapped_column(comment="Язык материала (ISO 639-1)")
    owner_note: Mapped[str | None] = mapped_column(
        comment="Что владелец написал про материал при отправке"
    )
    analysis_status: Mapped[str] = mapped_column(
        server_default=sql_text("'pending'"),
        comment="Состояние разбора: pending, analyzed, skipped, failed, quarantined",
    )
    quarantine_reason: Mapped[str | None] = mapped_column(
        comment="Почему материал в карантине (инъекция, не контент, автор заблокирован)"
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        comment="Владелец убрал материал. Его доказательства отзываются, веса пересчитываются"
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_account_id_source_id", "account_id", "source_id"),
        {"comment": "Текст, извлечённый из источника"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id"), comment="Источник текста"
    )
    text: Mapped[str] = mapped_column(comment="Исходный извлечённый текст")
    text_clean: Mapped[str | None] = mapped_column(comment="Текст после очистки")
    token_count: Mapped[int | None] = mapped_column(comment="Число токенов в очищенном тексте")
    created_at: Mapped[datetime] = created_at()


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("account_id", "document_id", "idx"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_text_trgm",
            "text",
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        ),
        {"comment": "Куски документов для поиска (полнотекстового, триграммного, векторного)"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id"), comment="Документ, из которого кусок"
    )
    idx: Mapped[int] = mapped_column(comment="Порядковый номер куска в документе, с 0")
    text: Mapped[str] = mapped_column(comment="Текст куска")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=sql_text("'{}'::text[]"), comment="Метки (тема, формат, приём)"
    )
    lang: Mapped[str | None] = mapped_column(comment="Язык куска")
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, text)", persisted=True),
        comment="Полнотекстовый вектор (генерируется из text)",
    )
    created_at: Mapped[datetime] = created_at()


class EmbeddingModel(Base):
    __tablename__ = "embedding_models"
    __table_args__ = (
        CheckConstraint("dup_threshold BETWEEN 0 AND 1", name="dup_threshold_range"),
        CheckConstraint("related_threshold BETWEEN 0 AND 1", name="related_threshold_range"),
        {"comment": "Реестр моделей эмбеддингов (глобальный)"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(unique=True, comment="Имя модели, например в fastembed")
    dim: Mapped[int] = mapped_column(comment="Размерность вектора")
    active: Mapped[bool] = mapped_column(
        server_default=sql_text("false"), comment="Используется для новых векторов и поиска"
    )
    created_at: Mapped[datetime] = created_at()
    dup_threshold: Mapped[Decimal] = mapped_column(
        Numeric,
        server_default=sql_text("0.88"),
        comment="Порог косинусного сходства «это одно и то же знание» для этой модели",
    )
    related_threshold: Mapped[Decimal] = mapped_column(
        Numeric,
        server_default=sql_text("0.70"),
        comment="Порог сходства «знания связаны, проверить судьёй» для этой модели",
    )


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("account_id", "chunk_id", "model_id"),
        Index(
            "ix_embeddings_vec_hnsw",
            "vec",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"vec": "vector_cosine_ops"},
        ),
        {"comment": "Векторы кусков (pgvector, HNSW по косинусу)"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"), comment="Кусок")
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("embedding_models.id"), comment="Модель, которой посчитан вектор"
    )
    vec: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), comment="Вектор (384 измерения)")
    created_at: Mapped[datetime] = created_at()


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (
        UniqueConstraint("account_id", "handle"),
        {"comment": "Аккаунты X, за которыми следим"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    handle: Mapped[str] = mapped_column(comment="Имя в X без @")
    x_user_id: Mapped[str | None] = mapped_column(comment="Числовой ID пользователя в X")
    priority: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Приоритет опроса (больше — чаще)"
    )
    active: Mapped[bool] = mapped_column(
        server_default=sql_text("true"), comment="Следим ли сейчас"
    )
    since_id: Mapped[str | None] = mapped_column(comment="Последний прочитанный ID поста")
    created_at: Mapped[datetime] = created_at()
