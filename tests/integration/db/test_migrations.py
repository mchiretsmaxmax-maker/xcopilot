"""Миграции: вверх с нуля, вниз до нуля, совпадение с моделями, сиды."""

import asyncio
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection

from tests.dbutil import fetch, table_names, view_names
from xcopilot.clock import SystemClock
from xcopilot.db.engine import create_engine
from xcopilot.db.migrate import alembic_config, include_object
from xcopilot.db.models import Base
from xcopilot.db.partitions import ensure_all_partitions

EXPECTED_TABLES = {
    # access
    "users",
    "accounts",
    "account_members",
    "secrets",
    # knowledge
    "sources",
    "documents",
    "chunks",
    "embeddings",
    "embedding_models",
    "competitors",
    # derived
    "style_passports",
    "facts",
    "insights",
    # content
    "topics",
    "generation_runs",
    "drafts",
    "reviews",
    "publications",
    # metrics
    "events",
    "post_metric_snapshots",
    "account_metric_snapshots",
    "metric_definitions",
    "metric_values",
    # ops
    "prompts",
    "llm_calls",
    "x_api_calls",
    "budgets",
    "jobs",
    "schedules",
    "bot_sessions",
    # «Мозг», ТЗ v3.1 (lessons удалена)
    "authors",
    "material_signals",
    "material_outcomes",
    "taxonomy_terms",
    "material_analyses",
    "knowledge_items",
    "knowledge_item_versions",
    "knowledge_item_scopes",
    "knowledge_evidence",
    "knowledge_links",
    "knowledge_transitions",
    "knowledge_embeddings",
    "knowledge_snapshots",
    "knowledge_packs",
    "knowledge_applications",
    "segments",
}
EXPECTED_VIEWS = {
    "v_post_checkpoints",
    "v_draft_funnel",
    "v_knowledge_usable",
    "v_knowledge_cards",
    "v_segment_items",
    "v_knowledge_evidence_summary",
    "v_material_best_signal",
}


async def _ensure_partitions(url: str) -> None:
    engine = create_engine(url)
    try:
        async with engine.begin() as conn:
            await ensure_all_partitions(conn, today=SystemClock().now().date())
    finally:
        await engine.dispose()


async def _diff(url: str) -> list[Any]:
    engine = create_engine(url)

    def compare(sync_conn: Connection) -> list[Any]:
        ctx = MigrationContext.configure(
            sync_conn, opts={"include_object": include_object, "compare_type": True}
        )
        return list(compare_metadata(ctx, Base.metadata))

    try:
        async with engine.connect() as conn:
            return await conn.run_sync(compare)
    finally:
        await engine.dispose()


def test_migrations_upgrade_from_empty(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "head")
    assert table_names(empty_db_url) >= EXPECTED_TABLES
    assert "lessons" not in table_names(empty_db_url)
    assert view_names(empty_db_url) >= EXPECTED_VIEWS
    exts = {r["extname"] for r in fetch(empty_db_url, "SELECT extname FROM pg_extension")}
    assert {"vector", "pg_trgm"} <= exts
    head = ScriptDirectory.from_config(cfg).get_current_head()
    [row] = fetch(empty_db_url, "SELECT version_num FROM alembic_version")
    assert row["version_num"] == head


def test_migrations_downgrade_to_base(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "head")
    asyncio.run(_ensure_partitions(empty_db_url))
    command.downgrade(cfg, "base")
    assert table_names(empty_db_url) - {"alembic_version"} == set()
    assert view_names(empty_db_url) == set()
    exts = {r["extname"] for r in fetch(empty_db_url, "SELECT extname FROM pg_extension")}
    assert not exts & {"vector", "pg_trgm"}


def test_models_match_migrations(empty_db_url: str) -> None:
    command.upgrade(alembic_config(empty_db_url), "head")
    asyncio.run(_ensure_partitions(empty_db_url))
    assert asyncio.run(_diff(empty_db_url)) == []


def test_seeds_present(db_url: str) -> None:
    [acc] = fetch(db_url, "SELECT id, handle FROM accounts WHERE kind = 'global'")
    assert acc["handle"] == "global"
    [model] = fetch(db_url, "SELECT name, dim, active FROM embedding_models")
    assert model["name"] == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert model["dim"] == 384 and model["active"]
    budgets = {
        (r["scope"], r["period"]): r["limit_micro_usd"]
        for r in fetch(
            db_url,
            "SELECT scope, period, limit_micro_usd FROM budgets WHERE account_id = $1",
            acc["id"],
        )
    }
    assert budgets == {("llm", "day"): 5_000_000, ("x_api", "day"): 1_000_000}
    kinds = {r["kind"] for r in fetch(db_url, "SELECT kind FROM schedules WHERE enabled")}
    assert "db.ensure_partitions" in kinds
