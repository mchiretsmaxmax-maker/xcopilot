"""SQL поиска по кускам: вектор (pgvector, HNSW, косинус) и полнотекст (tsvector, simple)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    ColumnElement,
    Float,
    String,
    Text,
    bindparam,
    cast,
    func,
    select,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from xcopilot.db.models import Chunk, Document, Draft, Embedding, EmbeddingModel, Source

_WORD = re.compile(r"\w+", re.UNICODE)
MAX_QUERY_WORDS = 32
EF_SEARCH_MIN = 100
OWN_VOICE_KINDS = ("own_post",)
COMMON_TERM_SHARE = 0.15


@dataclass(frozen=True)
class Hit:
    """Найденный кусок: текст, вид источника, метки и счёт (больше — лучше)."""

    chunk_id: UUID
    text: str
    kind: str
    source_id: UUID
    tags: tuple[str, ...]
    lang: str | None
    created_at: datetime
    score: float
    vec: tuple[float, ...] | None = None


@dataclass(frozen=True)
class HitFilters:
    """Фильтры поиска: метки (любая из), язык, интервал дат куска [since, until)."""

    tags: tuple[str, ...] = ()
    lang: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def query_words(query: str) -> list[str]:
    """Слова запроса (нижний регистр, без повторов, порядок сохранён) — безопасны для tsquery."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(query.lower()):
        seen.setdefault(word, None)
    return list(seen)[:MAX_QUERY_WORDS]


def _vec_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.8g}" for x in vec) + "]"


def _to_tuple(raw: Any) -> tuple[float, ...] | None:
    return None if raw is None else tuple(float(x) for x in raw)


class RetrievalRepo:
    """Поиск по кускам одного аккаунта (чужие куски не видны никогда)."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("RetrievalRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    # --- общие условия ------------------------------------------------------------------

    @staticmethod
    def own_voice() -> ColumnElement[bool]:
        """Единственное место предиката «свой голос».

        Сейчас: вид источника `own_post`. Когда в схеме появится `sources.stance`,
        достаточно заменить на `Source.stance == 'own'`.
        """
        return Source.kind.in_(OWN_VOICE_KINDS)

    def _restrict(
        self,
        stmt: Select[Any],
        kinds: Sequence[str] | None,
        filters: HitFilters,
        own_only: bool = False,
    ) -> Select[Any]:
        stmt = stmt.where(Chunk.account_id == self.account_id, Source.status == "indexed")
        if own_only:
            stmt = stmt.where(self.own_voice())
        if kinds is not None:
            stmt = stmt.where(Source.kind.in_(list(kinds)))
        if filters.lang is not None:
            stmt = stmt.where(Chunk.lang == filters.lang)
        if filters.tags:
            stmt = stmt.where(
                Chunk.tags.op("&&")(bindparam("ftags", list(filters.tags), type_=ARRAY(Text)))
            )
        if filters.since is not None:
            stmt = stmt.where(Chunk.created_at >= filters.since)
        if filters.until is not None:
            stmt = stmt.where(Chunk.created_at < filters.until)
        return stmt

    @staticmethod
    def _hit(row: Any, score: float) -> Hit:
        chunk: Chunk = row[0]
        return Hit(
            chunk_id=chunk.id,
            text=chunk.text,
            kind=row[1],
            source_id=row[2],
            tags=tuple(chunk.tags),
            lang=chunk.lang,
            created_at=chunk.created_at,
            score=score,
            vec=_to_tuple(row[3]),
        )

    def _vector_stmt(
        self,
        vec: Sequence[float],
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
        own_only: bool = False,
    ) -> Select[Any]:
        qvec = cast(bindparam("qvec", _vec_literal(vec), String), Vector(384))
        dist = Embedding.vec.op("<=>", return_type=Float)(qvec).label("dist")
        stmt = (
            select(Chunk, Source.kind, Source.id, Embedding.vec, dist)
            .select_from(Embedding)
            .join(EmbeddingModel, EmbeddingModel.id == Embedding.model_id)
            .join(Chunk, Chunk.id == Embedding.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(Embedding.account_id == self.account_id, EmbeddingModel.active.is_(True))
        )
        return self._restrict(stmt, kinds, filters, own_only).order_by(dist).limit(limit)

    # --- вектор -------------------------------------------------------------------------

    async def vector_hits(
        self,
        vec: Sequence[float],
        *,
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
        own_only: bool = False,
    ) -> list[Hit]:
        """Ближайшие по косинусу куски: score = 1 − расстояние; порядок (score↓, id)."""
        await self.session.execute(
            text(
                "SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true),"
                " set_config('hnsw.ef_search', :ef, true)"
            ),
            {"ef": str(max(EF_SEARCH_MIN, limit * 2))},
        )
        rows = (
            await self.session.execute(self._vector_stmt(vec, kinds, filters, limit, own_only))
        ).all()
        hits = [self._hit(r, 1.0 - float(r[4])) for r in rows]
        hits.sort(key=lambda h: (-h.score, str(h.chunk_id)))
        return hits

    async def explain_vector(
        self,
        vec: Sequence[float],
        *,
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
        force_index: bool,
    ) -> str:
        """План запроса векторного поиска.

        force_index выключает seq scan, bitmap и явную сортировку: на малых таблицах иначе
        планировщик не выберет индекс, а нас интересует, способен ли запрос его использовать.
        """
        stmt = self._vector_stmt(vec, kinds, filters, limit)
        dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
        sql = str(stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        if force_index:
            for knob in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
                await self.session.execute(text(f"SET LOCAL {knob} = off"))
        rows = (await self.session.execute(text("EXPLAIN " + sql.replace(":", r"\:")))).all()
        return "\n".join(r[0] for r in rows)

    # --- полнотекст ---------------------------------------------------------------------

    async def keyword_hits(
        self,
        query: str,
        *,
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
        own_only: bool = False,
    ) -> list[Hit]:
        """Куски с любым словом запроса (tsvector 'simple'); score = ts_rank_cd; порядок (↓, id)."""
        words = query_words(query)
        if not words:
            return []
        words = await self._drop_common(words)
        tsq = func.to_tsquery("simple", " | ".join(words))
        rank = func.ts_rank_cd(Chunk.tsv, tsq).label("rank")
        active = select(EmbeddingModel.id).where(EmbeddingModel.active.is_(True)).limit(1)
        stmt = (
            select(Chunk, Source.kind, Source.id, Embedding.vec, rank)
            .select_from(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .outerjoin(
                Embedding,
                (Embedding.account_id == self.account_id)
                & (Embedding.chunk_id == Chunk.id)
                & (Embedding.model_id == active.scalar_subquery()),
            )
            .where(Chunk.tsv.op("@@")(tsq))
        )
        stmt = self._restrict(stmt, kinds, filters, own_only)
        stmt = stmt.order_by(rank.desc(), Chunk.id).limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [self._hit(r, float(r[4])) for r in rows]

    async def _drop_common(self, words: list[str]) -> list[str]:
        """Выкинуть слова, которые есть в >15% кусков (статистика планировщика).

        Запрос «ИЛИ» с таким словом совпадает почти со всей таблицей и заставляет ранжировать
        десятки тысяч строк (сотни мс). Если выкинулось всё — слова остаются как были.
        """
        rows = await self.session.execute(
            text(
                "SELECT e FROM pg_stats s, unnest(s.most_common_elems::text::text[], "
                "s.most_common_elem_freqs) AS t(e, f) WHERE s.schemaname = 'public' "
                "AND s.tablename = 'chunks' AND s.attname = 'tsv' AND t.f > :share "
                "AND t.e IS NOT NULL"
            ),
            {"share": COMMON_TERM_SHARE},
        )
        common = {r[0] for r in rows.all()}
        kept = [w for w in words if w not in common]
        return kept or words

    # --- восстановление пакета ----------------------------------------------------------

    async def chunks_by_ids(self, ids: Sequence[UUID]) -> list[Hit]:
        """Свои куски по id в порядке `ids`; чужие и несуществующие пропускаются."""
        if not ids:
            return []
        stmt = (
            select(Chunk, Source.kind, Source.id, Chunk.id)
            .join(Document, Document.id == Chunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .where(Chunk.account_id == self.account_id, Chunk.id.in_(list(ids)))
        )
        by_id = {
            r[0].id: self._hit((r[0], r[1], r[2], None), 0.0)
            for r in (await self.session.execute(stmt)).all()
        }
        return [by_id[i] for i in ids if i in by_id]

    async def rejected_drafts(self, limit: int) -> list[tuple[UUID, str]]:
        """Свои отклонённые владельцем черновики, новые первыми: (id, текст)."""
        stmt = (
            select(Draft.id, Draft.text)
            .where(Draft.account_id == self.account_id, Draft.status == "rejected")
            .order_by(Draft.id.desc())
            .limit(limit)
        )
        return [(r[0], r[1]) for r in (await self.session.execute(stmt)).all()]

    async def drafts_by_ids(self, ids: Sequence[UUID]) -> list[tuple[UUID, str]]:
        """Свои черновики по id в порядке `ids`."""
        if not ids:
            return []
        stmt = select(Draft.id, Draft.text).where(
            Draft.account_id == self.account_id, Draft.id.in_(list(ids))
        )
        by_id = {r[0]: r[1] for r in (await self.session.execute(stmt)).all()}
        return [(i, by_id[i]) for i in ids if i in by_id]


__all__ = ["OWN_VOICE_KINDS", "Hit", "HitFilters", "RetrievalRepo", "query_words"]
