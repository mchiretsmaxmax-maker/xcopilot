"""Схемы писателя (этап 10): ответы модели и результаты для бота и обработчиков задач."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DraftFormat = Literal["post", "thread", "reply", "quote"]
FORMAT_NAMES: tuple[str, ...] = ("post", "thread", "reply", "quote")


# --- ответы модели ------------------------------------------------------------------------


class TopicIdea(BaseModel):
    """Одна тема от модели: угол, обоснование и формат."""

    title: str = Field(min_length=3, max_length=200)
    angle: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=500)
    format: DraftFormat = "post"


class TopicsOut(BaseModel):
    topics: list[TopicIdea] = Field(min_length=1, max_length=12)


class DraftOut(BaseModel):
    """Один вариант черновика: заход, текст (для треда — части)."""

    approach: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=25_000)
    parts: list[str] = Field(default_factory=list, max_length=12)


class DraftBatch(BaseModel):
    variants: list[DraftOut] = Field(min_length=1, max_length=5)


# --- результаты ---------------------------------------------------------------------------


class WhyItWorked(BaseModel):
    """Почему зашёл чужой пост — гипотеза из разбора `material_analyses` (этап 7A)."""

    available: bool = False
    label: Literal["гипотеза"] = "гипотеза"
    hook_type: str | None = None
    hook_text: str | None = None
    structure: str | None = None
    devices: list[str] = Field(default_factory=list)
    closer: str | None = None
    hypotheses: list[str] = Field(default_factory=list)
    analysis_id: UUID | None = None


class Topic(BaseModel):
    """Тема, записанная в `topics`."""

    id: UUID
    title: str
    angle: str | None = None
    rationale: str | None = None
    format: DraftFormat = "post"
    origin: str


class TargetPost(BaseModel):
    """Чужой пост, на который отвечаем или который цитируем."""

    text: str = Field(min_length=1)
    url: str | None = None
    author: str | None = None


class DraftView(BaseModel):
    """Записанный вариант черновика."""

    draft_id: UUID
    variant_no: int
    format: str
    text: str
    parts: list[str] = Field(default_factory=list)
    approach: str = ""
    probation_item_id: UUID | None = None
    probation_guidance: str | None = None
    issues: list[str] = Field(default_factory=list)


class GenerationRun(BaseModel):
    """Итог запуска генерации (строка `generation_runs` + черновики)."""

    run_id: UUID
    kind: str
    status: str
    drafts: list[DraftView] = Field(default_factory=list)
    analysis: WhyItWorked | None = None
    warnings: list[str] = Field(default_factory=list)
    reused: bool = False
    error: str | None = None
