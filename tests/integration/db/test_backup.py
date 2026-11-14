"""Бэкап (pg_dump -Fc) и восстановление дают ту же БД."""

import asyncio
import os
import subprocess
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer

from tests.dbutil import fetch, table_names
from xcopilot.clock import SystemClock
from xcopilot.db.engine import create_engine, session_factory
from xcopilot.db.partitions import ensure_all_partitions
from xcopilot.db.repos import AccountsRepo, SourcesRepo
from xcopilot.db.uow import UnitOfWork

ROOT = Path(__file__).resolve().parents[3]


async def _seed(url: str) -> None:
    engine = create_engine(url)
    try:
        async with UnitOfWork(session_factory(engine)) as uow:
            s: AsyncSession = uow.session
            acc = await AccountsRepo(s).create(handle="backup_test")
            for i in range(3):
                await SourcesRepo(s, acc.id).add_or_get(
                    kind="note", stance="reference", origin="manual_text", content_hash=f"h{i}"
                )
    finally:
        await engine.dispose()


async def _ensure_partitions(url: str) -> None:
    engine = create_engine(url)
    try:
        async with engine.begin() as conn:
            await ensure_all_partitions(conn, today=SystemClock().now().date())
    finally:
        await engine.dispose()


def _counts(url: str) -> dict[str, int]:
    return {
        t: fetch(url, f'SELECT count(*) AS n FROM "{t}"')[0]["n"] for t in sorted(table_names(url))
    }


def test_backup_restore_roundtrip(
    pg_container: PostgresContainer, db_url: str, empty_db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))
    before = _counts(db_url)
    assert before["accounts"] >= 2 and before["sources"] == 3
    env = {**os.environ, "PG_CONTAINER": pg_container.get_wrapped_container().id}
    dump = tmp_path / "xcopilot.dump"
    subprocess.run(
        [str(ROOT / "scripts/backup.sh"), str(dump)],
        env={**env, "DATABASE_URL": db_url},
        check=True,
        timeout=120,
    )
    assert dump.stat().st_size > 0
    assert dump.read_bytes()[:5] == b"PGDMP"  # custom-формат pg_dump -Fc
    subprocess.run(
        [str(ROOT / "scripts/restore.sh"), str(dump)],
        env={**env, "DATABASE_URL": empty_db_url},
        check=True,
        timeout=120,
    )
    assert _counts(empty_db_url) == before


def test_restore_over_existing_database_replaces_data(
    pg_container: PostgresContainer, db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_ensure_partitions(db_url))
    asyncio.run(_seed(db_url))
    before = _counts(db_url)
    env = {
        **os.environ,
        "PG_CONTAINER": pg_container.get_wrapped_container().id,
        "DATABASE_URL": db_url,
    }
    dump = tmp_path / "x.dump"
    subprocess.run([str(ROOT / "scripts/backup.sh"), str(dump)], env=env, check=True, timeout=120)
    fetch(db_url, "DELETE FROM documents")
    fetch(db_url, "DELETE FROM sources")
    assert _counts(db_url)["sources"] == 0
    subprocess.run(
        [str(ROOT / "scripts/restore.sh"), str(dump)],
        env=env,
        stdin=subprocess.DEVNULL,
        check=True,
        timeout=120,
    )
    assert _counts(db_url) == before
    children = fetch(db_url, "SELECT count(*) AS n FROM pg_inherits")
    assert children[0]["n"] >= 16  # 4 журнала × (3 месяца + DEFAULT)


def test_restore_rejects_missing_file(tmp_path: Path) -> None:
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/db"}
    result = subprocess.run(
        [str(ROOT / "scripts/restore.sh"), str(tmp_path / "nope.dump")],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 2 and "не найден" in result.stderr


def test_restore_of_truncated_dump_fails_and_keeps_data(
    pg_container: PostgresContainer, db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))
    before = _counts(db_url)
    env = {
        **os.environ,
        "PG_CONTAINER": pg_container.get_wrapped_container().id,
        "DATABASE_URL": db_url,
    }
    dump = tmp_path / "x.dump"
    subprocess.run([str(ROOT / "scripts/backup.sh"), str(dump)], env=env, check=True, timeout=120)
    broken = tmp_path / "broken.dump"
    broken.write_bytes(dump.read_bytes()[: dump.stat().st_size // 2])
    result = subprocess.run(
        [str(ROOT / "scripts/restore.sh"), str(broken)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert _counts(db_url) == before
