"""python -m xcopilot.db.migrate: миграции + партиции, идемпотентно."""

import pytest

from tests.dbutil import fetch
from xcopilot.db.migrate import main, upgrade
from xcopilot.db.partitions import PARTITIONED_TABLES


def test_upgrade_creates_partitions_once(empty_db_url: str) -> None:
    created = upgrade(empty_db_url)
    assert set(created) == set(PARTITIONED_TABLES)
    assert all(len(names) == 3 for names in created.values())
    assert upgrade(empty_db_url) == {t: [] for t in PARTITIONED_TABLES}
    [row] = fetch(empty_db_url, "SELECT count(*) AS n FROM accounts WHERE kind = 'global'")
    assert row["n"] == 1


def test_main_requires_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main() == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_main_migrates(
    monkeypatch: pytest.MonkeyPatch, empty_db_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_db_url)
    assert main() == 0
    # ТЗ v3.1: + knowledge_snapshots → 5 партиционированных таблиц × 3 месяца
    assert "новых партиций: 15" in capsys.readouterr().out


def _version(url: str) -> str:
    [row] = fetch(url, "SELECT version_num FROM alembic_version")
    return str(row["version_num"])


def test_migrate_cli_downgrade_to_revision(
    monkeypatch: pytest.MonkeyPatch, empty_db_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_db_url)
    assert main([]) == 0
    assert _version(empty_db_url) != "0009"
    assert main(["downgrade", "0009"]) == 0
    assert _version(empty_db_url) == "0009"


def test_migrate_cli_downgrade_requires_revision(
    monkeypatch: pytest.MonkeyPatch, empty_db_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_db_url)
    assert main([]) == 0
    head = _version(empty_db_url)
    assert main(["downgrade"]) == 2
    assert _version(empty_db_url) == head


def test_migrate_cli_refuses_base_without_flag(
    monkeypatch: pytest.MonkeyPatch, empty_db_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_db_url)
    assert main([]) == 0
    head = _version(empty_db_url)
    assert main(["downgrade", "base"]) == 2
    assert _version(empty_db_url) == head
    assert main(["downgrade", "base", "--yes"]) == 0
