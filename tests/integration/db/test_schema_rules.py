"""Правила схемы: тенант везде, комментарии, UUIDv7, индексы, деньги."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Глобальные таблицы без account_id (ТЗ, этап 2) и корень тенантов — сама accounts.
GLOBAL_TABLES = {"users", "embedding_models", "metric_definitions", "prompts"}
TENANT_ROOT = "accounts"

TABLES_SQL = """
SELECT c.oid, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND c.relname <> 'alembic_version'
ORDER BY c.relname
"""


async def test_every_business_table_has_account_id(engine: AsyncEngine) -> None:
    problems: list[str] = []
    async with engine.connect() as conn:
        tables = (await conn.execute(text(TABLES_SQL))).all()
        assert len(tables) >= 31
        for oid, name in tables:
            if name in GLOBAL_TABLES or name == TENANT_ROOT:
                continue
            col = (
                await conn.execute(
                    text(
                        "SELECT attnum, attnotnull FROM pg_attribute"
                        " WHERE attrelid = :oid AND attname = 'account_id' AND NOT attisdropped"
                    ),
                    {"oid": oid},
                )
            ).first()
            if col is None:
                problems.append(f"{name}: нет account_id")
                continue
            attnum, notnull = col
            if not notnull:
                problems.append(f"{name}: account_id допускает NULL")
            fk = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM pg_constraint WHERE conrelid = :oid AND contype = 'f'"
                        " AND conkey = ARRAY[:attnum]::int2[]"
                        " AND confrelid = 'accounts'::regclass"
                    ),
                    {"oid": oid, "attnum": attnum},
                )
            ).first()
            if fk is None:
                problems.append(f"{name}: нет внешнего ключа account_id -> accounts")
            idx = (
                await conn.execute(
                    text("SELECT 1 FROM pg_index WHERE indrelid = :oid AND indkey[0] = :attnum"),
                    {"oid": oid, "attnum": attnum},
                )
            ).first()
            if idx is None:
                problems.append(f"{name}: нет индекса, начинающегося с account_id")
    assert problems == []


async def test_every_table_and_column_has_comment(engine: AsyncEngine) -> None:
    problems: list[str] = []
    async with engine.connect() as conn:
        for oid, name in (await conn.execute(text(TABLES_SQL))).all():
            comment = (
                await conn.execute(text("SELECT obj_description(:oid, 'pg_class')"), {"oid": oid})
            ).scalar()
            if not comment:
                problems.append(f"{name}: нет комментария таблицы")
            rows = (
                await conn.execute(
                    text(
                        "SELECT attname, col_description(attrelid, attnum) FROM pg_attribute"
                        " WHERE attrelid = :oid AND attnum > 0 AND NOT attisdropped"
                    ),
                    {"oid": oid},
                )
            ).all()
            problems += [f"{name}.{col}: нет комментария" for col, c in rows if not c]
        views = (
            await conn.execute(
                text(
                    "SELECT c.relname, obj_description(c.oid, 'pg_class') FROM pg_class c"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'v'"
                )
            )
        ).all()
        assert len(views) >= 2
        problems += [f"{v}: нет комментария представления" for v, c in views if not c]
    assert problems == []


async def test_primary_keys_are_uuidv7_and_time_ordered(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        ids = []
        for i in range(5):
            row_id = (
                await conn.execute(
                    text("INSERT INTO users (tg_user_id, name) VALUES (:t, :n) RETURNING id"),
                    {"t": 1000 + i, "n": f"u{i}"},
                )
            ).scalar_one()
            ids.append(row_id)
        default = (
            await conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns"
                    " WHERE table_name = 'sources' AND column_name = 'id'"
                )
            )
        ).scalar_one()
    assert all(uuid.UUID(str(i)).version == 7 for i in ids)
    assert sorted(ids) == ids
    assert "uuidv7()" in default


async def test_hnsw_and_gin_indexes_exist(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tablename, indexdef FROM pg_indexes"
                    " WHERE schemaname = 'public' AND tablename IN ('embeddings', 'chunks')"
                )
            )
        ).all()
    defs = [(t, d) for t, d in rows]
    assert any(
        t == "embeddings" and "USING hnsw" in d and "vector_cosine_ops" in d for t, d in defs
    )
    assert any(t == "chunks" and "USING gin" in d and "(tsv)" in d for t, d in defs)
    assert any(t == "chunks" and "gin_trgm_ops" in d for t, d in defs)


async def test_money_columns_are_bigint_micro_usd(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.table_name, c.column_name, c.data_type"
                    " FROM information_schema.columns c"
                    " JOIN pg_class pc ON pc.relname = c.table_name"
                    " WHERE c.table_schema = 'public' AND NOT pc.relispartition"
                    " AND (c.column_name LIKE 'cost%' OR c.column_name LIKE '%micro_usd'"
                    "      OR c.column_name LIKE '%usd%' OR c.column_name LIKE '%price%')"
                )
            )
        ).all()
    money = {(t, c): dt for t, c, dt in rows}
    assert ("llm_calls", "cost_micro_usd") in money
    assert ("x_api_calls", "cost_micro_usd") in money
    assert ("budgets", "limit_micro_usd") in money
    bad = {k: dt for k, dt in money.items() if dt != "bigint" or not k[1].endswith("_micro_usd")}
    assert bad == {}
