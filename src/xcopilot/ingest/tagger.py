"""Теги кусков дешёвой моделью пачками: вид материала, темы, формат, язык."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.llm.gateway import LLMGateway

Material = Literal["own_post", "liked_sample", "competitor_post", "note"]
Format = Literal["short", "thread", "long", "list", "question", "announcement", "chat", "other"]
TAG_BATCH = 16
PROMPT_KEY = "ingest.tag"


class Tags(BaseModel):
    material: Material | None = None
    topics: list[str] = Field(default_factory=list)
    format: Format | None = None
    lang: str | None = None

    def as_db(self) -> list[str]:
        raise NotImplementedError


class TagBatch(BaseModel):
    items: list[Tags]


class Tagger:
    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    async def tag_batch(
        self, account_id: UUID, chunks: Sequence[str], *, own_handle: str | None = None
    ) -> list[Tags]:
        raise NotImplementedError
