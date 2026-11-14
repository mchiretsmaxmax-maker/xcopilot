"""Стенд ретривала: наполнение базы знаний напрямую в БД (источник → документ → кусок → вектор)."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeEmbedder

SessionFactory = async_sessionmaker[AsyncSession]
DIM = 384
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def axis(i: int, *, tilt: tuple[int, float] | None = None) -> list[float]:
    """Единичный вектор по оси i (с примесью другой оси: tilt=(j, доля))."""
    v = [0.0] * DIM
    v[i] = 1.0
    if tilt is not None:
        v[tilt[0]] = tilt[1]
    return v


@dataclass
class ScriptedEmbedder(FakeEmbedder):
    """FakeEmbedder, у которого для заданных текстов векторы заданы вручную."""

    vectors: dict[str, list[float]] = field(default_factory=dict)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        base = super().embed(texts)
        return [self.vectors.get(t, b) for t, b in zip(texts, base, strict=True)]


@dataclass
class Kb:
    """База знаний теста: создаёт аккаунты и куски с векторами."""

    sf: SessionFactory
    embedder: FakeEmbedder = field(default_factory=FakeEmbedder)

    async def account(self, handle: str) -> UUID:
        async with self.sf() as s:
            row = await s.execute(
                text(
                    "INSERT INTO accounts (handle, kind, niche, lang) "
                    "VALUES (:h, 'x', 'Крипта', 'ru') RETURNING id"
                ),
                {"h": handle},
            )
            await s.commit()
            return row.scalar_one()  # type: ignore[no-any-return]

    async def global_account(self) -> UUID:
        async with self.sf() as s:
            row = await s.execute(text("SELECT id FROM accounts WHERE kind = 'global'"))
            return row.scalar_one()  # type: ignore[no-any-return]

    async def chunk(
        self,
        account_id: UUID,
        body: str,
        *,
        kind: str = "own_post",
        tags: Sequence[str] = (),
        lang: str | None = "ru",
        created_at: datetime = T0,
        vec: Sequence[float] | None = None,
        status: str = "indexed",
    ) -> UUID:
        """Добавить один кусок с вектором (по умолчанию — от эмбеддера стенда)."""
        vector = list(vec) if vec is not None else self.embedder.embed([body])[0]
        key = uuid.uuid4().hex
        async with self.sf() as s:
            src = (
                await s.execute(
                    text(
                        "INSERT INTO sources (account_id, kind, origin, content_hash, status) "
                        "VALUES (:a, :k, 'manual', :h, :st) RETURNING id"
                    ),
                    {"a": account_id, "k": kind, "h": key, "st": status},
                )
            ).scalar_one()
            doc = (
                await s.execute(
                    text(
                        "INSERT INTO documents (account_id, source_id, text, text_clean) "
                        "VALUES (:a, :s, :t, :t) RETURNING id"
                    ),
                    {"a": account_id, "s": src, "t": body},
                )
            ).scalar_one()
            chunk_id = (
                await s.execute(
                    text(
                        "INSERT INTO chunks (account_id, document_id, idx, text, tags, lang, "
                        "created_at) VALUES (:a, :d, 0, :t, :tags, :lang, :ts) RETURNING id"
                    ),
                    {
                        "a": account_id,
                        "d": doc,
                        "t": body,
                        "tags": list(tags),
                        "lang": lang,
                        "ts": created_at,
                    },
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO embeddings (account_id, chunk_id, model_id, vec) "
                    "SELECT :a, :c, id, CAST(:v AS vector) FROM embedding_models WHERE active"
                ),
                {"a": account_id, "c": chunk_id, "v": json.dumps(vector)},
            )
            await s.commit()
            return chunk_id  # type: ignore[no-any-return]

    async def fact(
        self,
        account_id: UUID,
        statement: str,
        *,
        expires_at: datetime | None = None,
        observed_at: datetime = T0,
        source_url: str = "https://example.com/f",
    ) -> UUID:
        async with self.sf() as s:
            row = await s.execute(
                text(
                    "INSERT INTO facts (account_id, statement, source_url, observed_at, expires_at)"
                    " VALUES (:a, :s, :u, :o, :e) RETURNING id"
                ),
                {
                    "a": account_id,
                    "s": statement,
                    "u": source_url,
                    "o": observed_at,
                    "e": expires_at,
                },
            )
            await s.commit()
            return row.scalar_one()  # type: ignore[no-any-return]

    async def lesson(
        self,
        account_id: UUID,
        body: str,
        *,
        status: str = "accepted",
        weight: float = 1.0,
        kind: str = "do",
    ) -> UUID:
        async with self.sf() as s:
            row = await s.execute(
                text(
                    "INSERT INTO lessons (account_id, kind, text, status, weight) "
                    "VALUES (:a, :k, :t, :st, :w) RETURNING id"
                ),
                {"a": account_id, "k": kind, "t": body, "st": status, "w": weight},
            )
            await s.commit()
            return row.scalar_one()  # type: ignore[no-any-return]

    async def passport(
        self,
        account_id: UUID,
        *,
        version: int = 1,
        body: dict[str, Any] | None = None,
        status: str = "active",
    ) -> UUID:
        data = body if body is not None else default_passport_body()
        async with self.sf() as s:
            row = await s.execute(
                text(
                    "INSERT INTO style_passports (account_id, version, status, body, hard_rules, "
                    "created_by) VALUES (:a, :v, :st, CAST(:b AS jsonb), CAST(:h AS jsonb), 'llm')"
                    " RETURNING id"
                ),
                {
                    "a": account_id,
                    "v": version,
                    "st": status,
                    "b": json.dumps(data),
                    "h": json.dumps(data.get("hard_rules", {})),
                },
            )
            await s.commit()
            return row.scalar_one()  # type: ignore[no-any-return]


def default_passport_body() -> dict[str, Any]:
    return {
        "summary": "Короткие сухие посты без эмодзи",
        "tone": "иронично",
        "hard_rules": {"casing": "lower", "emoji": False, "max_len": 240},
        "anti_examples": ["Друзья! Сегодня поговорим о важном!", "🚀🚀 К луне!!!", "Третий"],
    }


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
