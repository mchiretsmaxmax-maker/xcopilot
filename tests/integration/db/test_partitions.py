"""Месячные партиции журналов и партиция DEFAULT."""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from xcopilot.db.partitions import (
    PARTITIONED_TABLES,
    default_partition_rowcount,
    ensure_all_partitions,
    ensure_partitions,
)

TODAY = date(2026, 11, 13)


async def _global_account(conn: AsyncConnection) -> UUID:
    row = await conn.execute(text("SELECT id FROM accounts WHERE kind = 'global'"))
    return UUID(str(row.scalar_one()))


async def _insert_event(conn: AsyncConnection, ts: datetime) -> str:
    row = await conn.execute(
        text(
            "INSERT INTO events (account_id, ts, type, actor)"
            " VALUES (:a, :ts, 'test.ping', 'system') RETURNING tableoid::regclass::text"
        ),
        {"a": await _global_account(conn), "ts": ts},
    )
    return str(row.scalar_one())


async def _children(conn: AsyncConnection, table: str) -> set[str]:
    rows = await conn.execute(
        text(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
            " WHERE i.inhparent = CAST(:t AS regclass)"
        ),
        {"t": table},
    )
    return set(rows.scalars().all())


async def test_partitions_created_ahead(engine: AsyncEngine) -> None:
    assert set(PARTITIONED_TABLES) == {
        "events",
        "post_metric_snapshots",
        "llm_calls",
        "x_api_calls",
        "knowledge_snapshots",  # ТЗ v3.1: снимки знаний, партиции по snapshot_date
    }
    async with engine.begin() as conn:
        created = await ensure_partitions(conn, "events", today=TODAY)
    assert set(created) == {"events_p202611", "events_p202612", "events_p202701"}
    async with engine.begin() as conn:
        assert {"events_p202611", "events_p202612", "events_p202701", "events_default"} <= (
            await _children(conn, "events")
        )
        assert await _insert_event(conn, datetime(2026, 12, 15, tzinfo=UTC)) == "events_p202612"
        assert await _insert_event(conn, datetime(2027, 1, 31, 23, 59, tzinfo=UTC)) == (
            "events_p202701"
        )
        assert await ensure_partitions(conn, "events", today=TODAY) == []
        result = await ensure_all_partitions(conn, today=TODAY)
    assert set(result) == set(PARTITIONED_TABLES)
    assert result["events"] == []
    assert set(result["llm_calls"]) == {
        "llm_calls_p202611",
        "llm_calls_p202612",
        "llm_calls_p202701",
    }


async def test_default_partition_catches_out_of_range_and_is_reported(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await ensure_all_partitions(conn, today=TODAY)
        assert await default_partition_rowcount(conn, "events") == 0
        part = await _insert_event(conn, datetime(2027, 11, 20, tzinfo=UTC))
        assert part == "events_default"
        assert await default_partition_rowcount(conn, "events") > 0
        assert await default_partition_rowcount(conn, "llm_calls") == 0


async def test_ensure_partitions_moves_rows_out_of_default(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        assert await _insert_event(conn, datetime(2027, 2, 10, tzinfo=UTC)) == "events_default"
        assert await default_partition_rowcount(conn, "events") == 1
        created = await ensure_partitions(conn, "events", months_ahead=0, today=date(2027, 2, 1))
        assert created == ["events_p202702"]
        assert await default_partition_rowcount(conn, "events") == 0
        moved = await conn.execute(
            text("SELECT tableoid::regclass::text FROM events WHERE type = 'test.ping'")
        )
        assert moved.scalar_one() == "events_p202702"
