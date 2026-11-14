"""Хвост тренда: разбор чужого поста из `material_analyses` (этап 7A) и проверка на копию."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from xcopilot.db.uow import DbSession
from xcopilot.writer.schemas import WhyItWorked


class PostRef(BaseModel):
    """Чужой пост: источник в базе, ссылка или текст."""

    source_id: UUID | None = None
    url: str | None = None
    text: str | None = None


class ResolvedPost(BaseModel):
    source_id: UUID | None = None
    url: str | None = None
    text: str


async def analysis_for(session: DbSession, account_id: UUID, source_id: UUID) -> WhyItWorked:
    raise NotImplementedError


async def resolve_post(session: DbSession, account_id: UUID, ref: PostRef) -> ResolvedPost:
    raise NotImplementedError


async def analyze(session: DbSession, account_id: UUID, post: ResolvedPost) -> WhyItWorked:
    raise NotImplementedError


def is_near_copy(text: str, original: str) -> bool:
    raise NotImplementedError
