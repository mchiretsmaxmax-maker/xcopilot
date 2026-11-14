"""Каталог метрик: разбор файлов, синхронизация, версии, READ ONLY."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.metrics.helpers import (
    COUNT_BODY,
    D,
    at,
    ev,
    make_account,
    write_definition,
)
from xcopilot.metrics.catalog import DEFINITIONS_DIR, load_definitions, sync_catalog
from xcopilot.metrics.errors import CatalogError, FormulaExecutionError, FormulaVersionError
from xcopilot.metrics.events import EventType
from xcopilot.metrics.rollup import compute


def test_load_definitions_parses_header(tmp_path: Path) -> None:
    write_definition(
        tmp_path,
        "m_one",
        COUNT_BODY,
        version=3,
        unit="ratio",
        extra_header="-- dims: reason_code, rule\n",
    )
    (definition,) = load_definitions(tmp_path)
    assert (definition.key, definition.unit, definition.grain, definition.version) == (
        "m_one",
        "ratio",
        "day",
        3,
    )
    assert definition.title == "Тест m_one"
    assert definition.dimensions == ["reason_code", "rule"]
    assert len(definition.formula_hash) == 64


def test_formula_must_return_required_columns(tmp_path: Path) -> None:
    body = "SELECT now()::date AS period_start, '{}'::jsonb AS dims, 1::numeric AS value;"
    write_definition(tmp_path, "no_sample", body)
    with pytest.raises(CatalogError, match="sample_size"):
        load_definitions(tmp_path)


def test_key_must_match_file_name(tmp_path: Path) -> None:
    path = write_definition(tmp_path, "m_one", COUNT_BODY)
    path.rename(tmp_path / "other.sql")
    with pytest.raises(CatalogError, match="key"):
        load_definitions(tmp_path)


def test_repo_catalog_has_17_metrics() -> None:
    # ТЗ v3.1: 17 − lessons_accepted_week + 12 метрик «Мозга» (приложение C) = 28
    definitions = load_definitions()
    assert len(definitions) == 28
    assert {p.stem for p in DEFINITIONS_DIR.glob("*.sql")} == {d.key for d in definitions}


async def test_catalog_sync_is_idempotent(session: AsyncSession) -> None:
    first = await sync_catalog(session)
    assert len(first.created) == 28 and not first.updated  # ТЗ v3.1: 17 − 1 + 12
    snapshot = (
        await session.execute(
            text(
                "SELECT key, version, formula_hash, updated_at FROM metric_definitions ORDER BY key"
            )
        )
    ).all()
    second = await sync_catalog(session)
    assert not second.created and not second.updated and len(second.unchanged) == 28
    again = (
        await session.execute(
            text(
                "SELECT key, version, formula_hash, updated_at FROM metric_definitions ORDER BY key"
            )
        )
    ).all()
    assert snapshot == again


async def test_formula_change_without_version_bump_fails(
    session: AsyncSession, tmp_path: Path
) -> None:
    write_definition(tmp_path, "m_one", COUNT_BODY)
    await sync_catalog(session, tmp_path)
    write_definition(
        tmp_path, "m_one", COUNT_BODY.replace("count(*)::numeric", "count(*)::numeric + 1")
    )
    with pytest.raises(FormulaVersionError):
        await sync_catalog(session, tmp_path)
    # с новой версией — принимается
    write_definition(
        tmp_path,
        "m_one",
        COUNT_BODY.replace("count(*)::numeric", "count(*)::numeric + 1"),
        version=2,
    )
    result = await sync_catalog(session, tmp_path)
    assert result.updated == ["m_one"]
    row = (
        await session.execute(text("SELECT version FROM metric_definitions WHERE key = 'm_one'"))
    ).scalar_one()
    assert row == 2


async def test_formula_executed_read_only(session: AsyncSession, tmp_path: Path) -> None:
    account_id = await make_account(session, "ro")
    body = """WITH ins AS (
  INSERT INTO events (account_id, type, actor) VALUES (:account_id, 'review.approved', 'system')
  RETURNING 1
)
SELECT CAST(:from AS timestamptz)::date AS period_start, '{}'::jsonb AS dims,
       (SELECT count(*) FROM ins)::numeric AS value, 1 AS sample_size;"""
    write_definition(tmp_path, "evil", body)
    await sync_catalog(session, tmp_path)
    with pytest.raises(FormulaExecutionError):
        await compute(session, "evil", account_id, D, D)
    await session.rollback()
    count = (await session.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert count == 0
    # сессия после ошибки рабочая и не осталась в READ ONLY
    account_id = await make_account(session, "ro2")
    await ev(session, account_id, EventType.REVIEW_APPROVED, at(9))
    await session.commit()


async def test_new_metric_needs_only_a_file(session: AsyncSession, tmp_path: Path) -> None:
    account_id = await make_account(session, "newfile")
    await ev(session, account_id, EventType.REVIEW_APPROVED, at(9))
    await ev(session, account_id, EventType.REVIEW_APPROVED, at(11))
    write_definition(tmp_path, "approved_count", COUNT_BODY, extra_header="")
    await sync_catalog(session, tmp_path)
    rows = await compute(session, "approved_count", account_id, D, D)
    assert [(r.period_start, r.value, r.sample_size) for r in rows] == [
        (date(2026, 3, 10), Decimal(2), 2)
    ]
