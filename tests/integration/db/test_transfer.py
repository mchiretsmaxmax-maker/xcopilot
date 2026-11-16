"""feat/device-transfer: перенос на другое устройство — пакет .xcbundle (БД + .env + data/)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from testcontainers.community.postgres import PostgresContainer

from tests.dbutil import fetch
from tests.integration.db.test_backup import ROOT, _counts, _seed

BOT_TOKEN = "987654:AAH-very-secret-bot-token"
ENV_TEXT = f"BOT_TOKEN={BOT_TOKEN}\nSECRETS_KEY=abc123\n"
TABLES = ("sources", "drafts", "knowledge_items")


def _env(pg: PostgresContainer, home: Path, passfile: Path, db_url: str) -> dict[str, str]:
    return {
        **os.environ,
        "PG_CONTAINER": pg.get_wrapped_container().id,
        "DATABASE_URL": db_url,
        "XC_ENV_FILE": str(home / ".env"),
        "XC_DATA_DIR": str(home / "data"),
        "XC_TRANSFER_PASSPHRASE_FILE": str(passfile),
        "XC_SKIP_SERVICE": "1",
        "XC_SKIP_SETUP": "1",
    }


def _passfile(tmp_path: Path, text: str = "учебный-пароль-переноса\n") -> Path:
    p = tmp_path / "pass"
    p.write_text(text)
    p.chmod(0o600)
    return p


def _old_device(tmp_path: Path) -> Path:
    home = tmp_path / "old"
    (home / "data" / "ingest").mkdir(parents=True)
    (home / ".env").write_text(ENV_TEXT)
    (home / "data" / "ingest" / "note.txt").write_text("материал")
    return home


def _export(pg: PostgresContainer, db_url: str, tmp_path: Path) -> tuple[Path, Path]:
    home = _old_device(tmp_path)
    bundle = tmp_path / "out.xcbundle"
    subprocess.run(
        [str(ROOT / "scripts/transfer-export.sh"), str(bundle)],
        env=_env(pg, home, _passfile(tmp_path), db_url),
        stdin=subprocess.DEVNULL,
        check=True,
        timeout=180,
    )
    return home, bundle


def _import(
    pg: PostgresContainer, db_url: str, home: Path, bundle: Path, passfile: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROOT / "scripts/transfer-import.sh"), str(bundle), *args],
        env=_env(pg, home, passfile, db_url),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_transfer_export_import_roundtrip(
    pg_container: PostgresContainer, db_url: str, empty_db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))
    before = _counts(db_url)
    assert all(before[t] >= 0 for t in TABLES) and before["sources"] == 3
    _, bundle = _export(pg_container, db_url, tmp_path)
    assert bundle.stat().st_mode & 0o077 == 0

    new = tmp_path / "new"
    new.mkdir()
    result = _import(pg_container, empty_db_url, new, bundle, _passfile(tmp_path))
    assert result.returncode == 0, result.stderr
    after = _counts(empty_db_url)
    assert all(after[t] == before[t] for t in TABLES)
    assert (new / ".env").read_bytes() == ENV_TEXT.encode()
    assert (new / ".env").stat().st_mode & 0o077 == 0
    assert (new / "data" / "ingest" / "note.txt").read_text() == "материал"


def test_transfer_bundle_is_encrypted(
    pg_container: PostgresContainer, db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))
    _, bundle = _export(pg_container, db_url, tmp_path)
    data = bundle.read_bytes()
    assert data[:8] == b"Salted__"
    assert BOT_TOKEN.encode() not in data and b"PGDMP" not in data


def test_transfer_import_refuses_wrong_passphrase(
    pg_container: PostgresContainer, db_url: str, empty_db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))
    _, bundle = _export(pg_container, db_url, tmp_path)
    new = tmp_path / "new"
    new.mkdir()
    (new / ".env").write_text("OLD=1\n")
    result = _import(pg_container, empty_db_url, new, bundle, _passfile(tmp_path, "не-тот\n"))
    assert result.returncode == 2, result.stderr
    assert (new / ".env").read_text() == "OLD=1\n"
    assert not (new / "data").exists()
    assert (
        fetch(
            empty_db_url,
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public'",
        )[0]["n"]
        == 0
    )


def test_transfer_import_refuses_non_empty_db_without_flag(
    pg_container: PostgresContainer, db_url: str, tmp_path: Path
) -> None:
    asyncio.run(_seed(db_url))  # в целевой БД уже есть рабочий аккаунт
    _, bundle = _export(pg_container, db_url, tmp_path)
    new = tmp_path / "new"
    new.mkdir()
    before = _counts(db_url)
    result = _import(pg_container, db_url, new, bundle, _passfile(tmp_path))
    assert result.returncode == 2, result.stderr
    assert "--force" in result.stderr
    assert not (new / ".env").exists()
    assert _counts(db_url) == before
