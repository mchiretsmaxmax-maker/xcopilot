"""Теги кусков дешёвой моделью пачками: вид материала, темы, формат, язык.

Сбой модели не мешает ингесту: пачка получает пустые теги (событие `llm.failed` пишет шлюз).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xcopilot.errors import BudgetExceeded
from xcopilot.llm.errors import LLMError
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted

log = structlog.get_logger()

Material = Literal["own_post", "liked_sample", "competitor_post", "note"]
Format = Literal["short", "thread", "long", "list", "question", "announcement", "chat", "other"]
TAG_BATCH = 16
PROMPT_KEY = "ingest.tag"
MAX_TOPICS = 5
MAX_TOPIC_LEN = 40


class Tags(BaseModel):
    model_config = ConfigDict(extra="ignore")

    material: Material | None = None
    topics: list[str] = Field(default_factory=list)
    format: Format | None = None
    lang: str | None = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for raw in value:
            topic = re.sub(r"\s+", " ", raw).strip().lower()[:MAX_TOPIC_LEN].strip()
            if topic and topic not in out:
                out.append(topic)
        return out[:MAX_TOPICS]

    @field_validator("lang")
    @classmethod
    def _check_lang(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lang = value.strip().lower()
        if not re.fullmatch(r"[a-z]{2}", lang):
            raise ValueError("lang — две латинские буквы")
        return lang

    def as_db(self) -> list[str]:
        """Метки для chunks.tags: material:…, format:…, topic:…."""
        tags: list[str] = []
        if self.material:
            tags.append(f"material:{self.material}")
        if self.format:
            tags.append(f"format:{self.format}")
        tags += [f"topic:{t}" for t in self.topics]
        return tags


class TagBatch(BaseModel):
    items: list[Tags]


class Tagger:
    def __init__(self, gateway: LLMGateway, batch_size: int = TAG_BATCH) -> None:
        self._gateway = gateway
        self._batch = batch_size

    async def tag_batch(
        self, account_id: UUID, chunks: Sequence[str], *, own_handle: str | None = None
    ) -> list[Tags]:
        """Теги для каждого куска по порядку; при сбое модели — пустые Tags для пачки."""
        out: list[Tags] = []
        for start in range(0, len(chunks), self._batch):
            part = chunks[start : start + self._batch]
            out += await self._tag_part(account_id, part, own_handle)
        return out

    async def _tag_part(
        self, account_id: UUID, part: Sequence[str], own_handle: str | None
    ) -> list[Tags]:
        items = "\n\n".join(wrap_untrusted(text, f"chunk_{i}") for i, text in enumerate(part))
        try:
            batch = await self._gateway.generate(
                role=Role.CHEAP,
                prompt_key=PROMPT_KEY,
                variables={
                    "items": items,
                    "count": str(len(part)),
                    "own_handle": f"@{own_handle}" if own_handle else "—",
                },
                schema=TagBatch,
                account_id=account_id,
                purpose=PROMPT_KEY,
            )
        except (LLMError, BudgetExceeded) as exc:
            log.warning("ingest_tag_failed", error=type(exc).__name__, chunks=len(part))
            return [Tags() for _ in part]
        if len(batch.items) != len(part):
            log.warning("ingest_tag_count_mismatch", got=len(batch.items), want=len(part))
            return [Tags() for _ in part]
        return batch.items
