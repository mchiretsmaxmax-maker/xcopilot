"""Помощники тестов «Мозга» (ТЗ v3.1): строки через SQL, без репозиториев."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.repos import AccountsRepo, DraftsRepo, RunsRepo


async def account(session: AsyncSession, handle: str | None = None) -> UUID:
    acc = await AccountsRepo(session).create(handle=handle or f"a{uuid4().hex[:10]}", tz="UTC")
    return acc.id


async def user(session: AsyncSession) -> UUID:
    return cast(
        UUID,
        (
            await session.execute(
                text("INSERT INTO users (tg_user_id, name) VALUES (:t, 'u') RETURNING id"),
                {"t": uuid4().int % 10**12},
            )
        ).scalar_one(),
    )


async def insert(session: AsyncSession, table: str, **values: Any) -> UUID:
    """INSERT … RETURNING id (значения — параметры запроса)."""
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return cast(
        UUID,
        (
            await session.execute(
                text(f"INSERT INTO {table} ({cols}) VALUES ({params}) RETURNING id"), values
            )
        ).scalar_one(),
    )


async def author(session: AsyncSession, acc: UUID, handle: str, **extra: Any) -> UUID:
    return await insert(session, "authors", account_id=acc, handle=handle, **extra)


async def source(
    session: AsyncSession,
    acc: UUID,
    *,
    kind: str = "post",
    stance: str = "inspiration",
    origin: str = "manual_text",
    **extra: Any,
) -> UUID:
    return await insert(
        session,
        "sources",
        account_id=acc,
        kind=kind,
        stance=stance,
        origin=origin,
        content_hash=uuid4().hex,
        **extra,
    )


async def item(
    session: AsyncSession,
    acc: UUID,
    *,
    layer: str = "craft",
    kind: str = "thesis",
    element: str = "hook",
    risk_class: str | None = None,
    target_metric: str = "engagement",
    status: str = "candidate",
    **extra: Any,
) -> UUID:
    return await insert(
        session,
        "knowledge_items",
        account_id=acc,
        kind=kind,
        layer=layer,
        element=element,
        statement="Личная цифра в первой строке даёт больше ответов",
        guidance="Начни с личной цифры",
        target_metric=target_metric,
        risk_class=risk_class or ("A" if layer == "voice" else "B"),
        status=status,
        origin="materials",
        **extra,
    )


async def evidence(
    session: AsyncSession,
    acc: UUID,
    item_id: UUID,
    *,
    observed_at: datetime,
    dimension: str = "eff",
    basis: str = "observed_outcome",
    polarity: int = 1,
    w_source: float = 1.0,
    w_scope: float = 1.0,
    w_magnitude: float = 1.0,
    **extra: Any,
) -> UUID:
    return await insert(
        session,
        "knowledge_evidence",
        account_id=acc,
        item_id=item_id,
        dimension=dimension,
        basis=basis,
        polarity=polarity,
        w_source=w_source,
        w_scope=w_scope,
        w_magnitude=w_magnitude,
        observed_at=observed_at,
        **{"created_at": observed_at, **extra},
    )


async def draft(session: AsyncSession, acc: UUID) -> UUID:
    run = await RunsRepo(session, acc).add(kind="post")
    d = await DraftsRepo(session, acc).add(run_id=run.id, format="post", text="t")
    return d.id


async def pack(session: AsyncSession, acc: UUID, items: str = "[]", task: str = "{}") -> UUID:
    return cast(
        UUID,
        (
            await session.execute(
                text(
                    "INSERT INTO knowledge_packs (account_id, consumer, task, schema_version,"
                    " taxonomy_version, items, token_count)"
                    " VALUES (:a, 'writer', CAST(:task AS jsonb), 1, 1, CAST(:items AS jsonb), 100)"
                    " RETURNING id"
                ),
                {"a": acc, "task": task, "items": items},
            )
        ).scalar_one(),
    )


async def scalar(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).scalar()
