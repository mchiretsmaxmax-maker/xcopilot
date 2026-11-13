"""Знания (сырьё): источники, тексты, куски, векторы, конкуренты. Сырьё не изменяется."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Computed, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from xcopilot.db.models.base import Base, account_fk, created_at, jsonb, one_of, uuid_pk

EMBEDDING_DIM = 384
SOURCE_KINDS = ("own_post", "liked_sample", "competitor_post", "file", "chat", "note")
SOURCE_ORIGINS = ("manual", "x_api", "oembed", "radar")
SOURCE_STATUSES = ("new", "parsed", "indexed", "failed", "excluded")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("account_id", "content_hash"),
        Index("ix_sources_account_id_kind_created_at", "account_id", "kind", "created_at"),
        one_of("kind", SOURCE_KINDS),
        one_of("origin", SOURCE_ORIGINS),
        one_of("status", SOURCE_STATUSES),
        {"comment": "Всё, что пришло в систему: посты, образцы, файлы, заметки"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = account_fk()
    kind: Mapped[str] = mapped_column(
        comment="Что это: own_post / liked_sample / competitor_post / file / chat / note"
    )
    origin: Mapped[str] = mapped_column(comment="Откуда: manual / x_api / oembed / radar")
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
    __table_args__ = ({"comment": "Реестр моделей эмбеддингов (глобальный)"},)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(unique=True, comment="Имя модели, например в fastembed")
    dim: Mapped[int] = mapped_column(comment="Размерность вектора")
    active: Mapped[bool] = mapped_column(
        server_default=sql_text("false"), comment="Используется для новых векторов и поиска"
    )
    created_at: Mapped[datetime] = created_at()


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
