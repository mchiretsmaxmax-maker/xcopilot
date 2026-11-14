"""Контекстный пакет писателя: паспорт, примеры голоса, уроки, факты, антипримеры, референсы."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder

DEFAULT_BUDGET = 6000
VOICE_KINDS = ("own_post",)
REFERENCE_KINDS = ("liked_sample", "competitor_post")
MIN_EXAMPLES = 4


class PackChunk(BaseModel):
    chunk_id: UUID
    kind: str
    text: str


class PackLesson(BaseModel):
    lesson_id: UUID
    kind: str
    text: str
    weight: float
    scope: Literal["account", "global"]


class PackFact(BaseModel):
    fact_id: UUID
    statement: str
    value: str | None = None
    unit: str | None = None
    source_url: str
    expires_at: datetime | None = None


class PackIds(BaseModel):
    """То, что хранится в `generation_runs.context_pack`: по этому можно восстановить пакет."""

    topic: str
    fmt: str
    budget_tokens: int
    passport_id: UUID | None = None
    passport_version: int | None = None
    examples: list[UUID] = Field(default_factory=list)
    references: list[UUID] = Field(default_factory=list)
    lessons: list[UUID] = Field(default_factory=list)
    facts: list[UUID] = Field(default_factory=list)
    anti_examples: int = 0
    warnings: list[str] = Field(default_factory=list)
    trimmed: dict[str, int] = Field(default_factory=dict)


class ContextPack(BaseModel):
    topic: str
    fmt: str
    budget_tokens: int
    tokens: int = 0
    passport_id: UUID | None = None
    passport_version: int | None = None
    passport: dict[str, Any] | None = None
    hard_rules: Any = None
    examples: list[PackChunk] = Field(default_factory=list)
    lessons: list[PackLesson] = Field(default_factory=list)
    facts: list[PackFact] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    references: list[PackChunk] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trimmed: dict[str, int] = Field(default_factory=dict)

    def to_ids(self) -> PackIds:
        raise NotImplementedError

    def render(self) -> str:
        raise NotImplementedError


async def build_pack(
    session: DbSession,
    account_id: UUID,
    topic: str,
    fmt: str,
    *,
    embedder: Embedder,
    now: datetime,
    budget_tokens: int = DEFAULT_BUDGET,
) -> ContextPack:
    raise NotImplementedError


async def rehydrate(session: DbSession, account_id: UUID, pack_ids: PackIds) -> ContextPack:
    raise NotImplementedError
