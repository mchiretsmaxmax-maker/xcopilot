"""Этап 15: ежедневный бэкап — зашифрованный дамп, копия на другой хост, отметка для алертов."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from testcontainers.community.postgres import PostgresContainer

from tests.dbutil import fetch
from tests.integration.db.test_backup import ROOT, _counts, _seed


def test_backup_script_produces_restorable_dump(
    pg_container: PostgresContainer, db_url: str, empty_db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))
    before = _counts(db_url)
    passfile = tmp_path / "backup.pass"
    passfile.write_text("учебный-пароль-бэкапа\n")
    passfile.chmod(0o600)
    remote = tmp_path / "remote-host"
    remote.mkdir()
    local = tmp_path / "backups"
    env = {
        **os.environ,
        "PG_CONTAINER": pg_container.get_wrapped_container().id,
        "BACKUP_DIR": str(local),
        "BACKUP_PASSPHRASE_FILE": str(passfile),
        "BACKUP_REMOTE": f"{remote}/",
        "BACKUP_KEEP_DAYS": "14",
    }
    subprocess.run(
        [str(ROOT / "scripts/backup.sh")],
        env={**env, "DATABASE_URL": db_url},
        check=True,
        timeout=120,
    )
    [dump] = sorted(local.glob("xcopilot-*.dump.enc"))
    data = dump.read_bytes()
    assert data[:5] != b"PGDMP" and data[:8] == b"Salted__"  # зашифрован, не голый дамп
    assert dump.stat().st_mode & 0o077 == 0  # читает только владелец
    [copy] = list(remote.glob("xcopilot-*.dump.enc"))
    assert copy.read_bytes() == data  # копия на «другом хосте» совпадает побайтно

    # отметка об успехе — для алерта «бэкап не прошёл» и /health
    marks = fetch(
        db_url,
        "SELECT m.key FROM ops_marks m JOIN accounts a ON a.id = m.account_id"
        " WHERE a.kind = 'global' AND m.key = 'backup.ok'",
    )
    assert len(marks) == 1

    # учебное восстановление по runbook: в пустую БД, с тем же паролем
    subprocess.run(
        [str(ROOT / "scripts/restore.sh"), str(copy)],
        env={**env, "DATABASE_URL": empty_db_url},
        stdin=subprocess.DEVNULL,
        check=True,
        timeout=120,
    )
    assert _counts(empty_db_url) == before

    # без пароля зашифрованный дамп не восстанавливается и данные не трогаются
    no_pass = {k: v for k, v in env.items() if k != "BACKUP_PASSPHRASE_FILE"}
    result = subprocess.run(
        [str(ROOT / "scripts/restore.sh"), str(copy)],
        env={**no_pass, "DATABASE_URL": empty_db_url},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2 and "BACKUP_PASSPHRASE_FILE" in result.stderr
    assert _counts(empty_db_url) == before
