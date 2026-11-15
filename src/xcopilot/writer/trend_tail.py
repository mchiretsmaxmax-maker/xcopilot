"""Хвост тренда: разбор чужого поста из `material_analyses` (этап 7A) и проверка на копию.

ТЗ v3.1: отдельного вызова модели для разбора нет — `analyze()` читает текущую строку
`material_analyses` источника. Нет строки (пост не в базе или ещё не разобран) — разбор
помечается недоступным, генерация идёт по оригиналу и паспорту. Функции 7A не импортируются.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.db.repos.materials import MaterialAnalysesRepo
from xcopilot.db.repos.sources import SourcesRepo
from xcopilot.db.repos.writer import WriterRepo
from xcopilot.db.uow import DbSession
from xcopilot.writer.schemas import WhyItWorked
from xcopilot.writer.similarity import TAIL_MAX, text_similarity


class PostRef(BaseModel):
    """Чужой пост: источник в базе, ссылка или текст."""

    source_id: UUID | None = None
    url: str | None = None
    text: str | None = None


class ResolvedPost(BaseModel):
    source_id: UUID | None = None
    url: str | None = None
    text: str


class PostNotFound(LookupError):
    """По ссылке или id поста нет в базе, а текста не прислали."""


def _claim(item: Any) -> str | None:
    if isinstance(item, dict):
        claim = item.get("claim")
        return str(claim) if claim else None
    return str(item) if item else None


async def analysis_for(session: DbSession, account_id: UUID, source_id: UUID) -> WhyItWorked:
    """Текущий разбор источника как гипотеза «почему зашёл»; нет разбора — недоступен."""
    row = await MaterialAnalysesRepo(session, account_id).current(source_id)
    if row is None:
        return WhyItWorked(available=False)
    return WhyItWorked(
        available=True,
        hook_type=row.hook_type,
        hook_text=row.hook_text,
        structure=row.structure,
        devices=list(row.devices or []),
        closer=row.closer,
        hypotheses=[c for c in (_claim(h) for h in row.why_hypotheses or []) if c],
        analysis_id=row.id,
    )


async def _source_text(session: DbSession, account_id: UUID, source_id: UUID) -> str | None:
    docs = await SourcesRepo(session, account_id).documents(source_id)
    texts = [d.text_clean or d.text for d in docs if (d.text_clean or d.text)]
    return "\n\n".join(texts) if texts else None


async def resolve_post(session: DbSession, account_id: UUID, ref: PostRef) -> ResolvedPost:
    """Найти пост: по id источника, по ссылке среди источников аккаунта или взять текст."""
    sources = SourcesRepo(session, account_id)
    source_id = ref.source_id
    if source_id is None and ref.url:
        source_id = await WriterRepo(session, account_id).source_id_by_url(ref.url)
    source = await sources.get(source_id) if source_id is not None else None
    if source is not None:
        body = await _source_text(session, account_id, source.id)
        if body:
            return ResolvedPost(source_id=source.id, url=source.url or ref.url, text=body)
    if ref.text and ref.text.strip():
        return ResolvedPost(url=ref.url, text=ref.text.strip())
    raise PostNotFound("пост не найден в базе")


async def analyze(session: DbSession, account_id: UUID, post: ResolvedPost) -> WhyItWorked:
    """Разбор поста (v3.1: чтение `material_analyses`, без вызова модели)."""
    if post.source_id is None:
        return WhyItWorked(available=False)
    return await analysis_for(session, account_id, post.source_id)


def is_near_copy(text: str, original: str) -> bool:
    """Новый текст слишком похож на оригинал (сходство ≥ 0.6)."""
    return text_similarity(text, original) >= TAIL_MAX


def why_summary(why: WhyItWorked) -> str:
    """Коротко для владельца: гипотезы или зачин/структура/приёмы."""
    if why.hypotheses:
        return "; ".join(why.hypotheses)
    parts = [p for p in (why.hook_type, why.structure, *why.devices, why.closer) if p]
    return ", ".join(parts)
