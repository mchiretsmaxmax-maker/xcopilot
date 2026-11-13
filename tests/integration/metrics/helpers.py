"""Общие помощники тестов метрик: аккаунты, события, определения во временной папке."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.repos import AccountsRepo
from xcopilot.metrics.events import EventType, emit

D = date(2026, 3, 10)


def at(hour: int, minute: int = 0, day: date = D) -> datetime:
    """Момент UTC в указанный день."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


async def make_account(session: AsyncSession, handle: str, tz: str = "UTC") -> UUID:
    acc = await AccountsRepo(session).create(handle=handle, tz=tz)
    return acc.id


async def ev(
    session: AsyncSession,
    account_id: UUID,
    type_: EventType,
    ts: datetime,
    *,
    entity_id: UUID | None = None,
    entity_type: str = "draft",
    **props: Any,
) -> UUID:
    return await emit(
        session,
        account_id=account_id,
        type=type_,
        ts=ts,
        entity=(entity_type, entity_id) if entity_id else None,
        props=props,
    )


async def llm_call(
    session: AsyncSession, account_id: UUID, ts: datetime, role: str, cost: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO llm_calls (account_id, ts, role, provider, model, purpose,"
            " cost_micro_usd, status) VALUES (:a, :ts, :role, 'anthropic', 'm', 'test', :c, 'ok')"
        ),
        {"a": account_id, "ts": ts, "role": role, "c": cost},
    )


async def x_call(
    session: AsyncSession, account_id: UUID, ts: datetime, endpoint: str, cost: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO x_api_calls (account_id, ts, endpoint, cost_micro_usd, utc_day, status)"
            " VALUES (:a, :ts, :e, :c, :d, 'ok')"
        ),
        {"a": account_id, "ts": ts, "e": endpoint, "c": cost, "d": ts.date()},
    )


async def snapshot(
    session: AsyncSession,
    account_id: UUID,
    x_post_id: str,
    published: datetime,
    age_minutes: int,
    *,
    impressions: int,
    likes: int = 0,
    replies: int = 0,
    reposts: int = 0,
    quotes: int = 0,
    bookmarks: int = 0,
) -> None:
    await session.execute(
        text(
            "INSERT INTO post_metric_snapshots (account_id, x_post_id, ts, age_minutes,"
            " impressions, likes, replies, reposts, quotes, bookmarks)"
            " VALUES (:a, :p, :ts, :age, :imp, :l, :r, :rp, :q, :b)"
        ),
        {
            "a": account_id,
            "p": x_post_id,
            "ts": published + timedelta(minutes=age_minutes),
            "age": age_minutes,
            "imp": impressions,
            "l": likes,
            "r": replies,
            "rp": reposts,
            "q": quotes,
            "b": bookmarks,
        },
    )


def new_id() -> UUID:
    return uuid4()


def write_definition(
    folder: Path,
    key: str,
    body: str,
    *,
    version: int = 1,
    unit: str = "count",
    grain: str = "day",
    extra_header: str = "",
) -> Path:
    path = folder / f"{key}.sql"
    path.write_text(
        f"-- key: {key}\n-- title: Тест {key}\n-- unit: {unit} · grain: {grain}"
        f" · version: {version}\n{extra_header}{body}\n"
    )
    return path


COUNT_BODY = """SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id AND e.ts >= :from AND e.ts < :to
GROUP BY 1;"""
