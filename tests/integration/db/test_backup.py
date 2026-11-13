"""Бэкап (pg_dump -Fc) и восстановление дают ту же БД."""

import asyncio
import os
import subprocess
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer

from tests.dbutil import fetch, table_names
from xcopilot.db.engine import create_engine, session_factory
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
                    kind="note", origin="manual", content_hash=f"h{i}"
                )
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
