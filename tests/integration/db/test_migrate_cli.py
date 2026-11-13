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
    assert "новых партиций: 12" in capsys.readouterr().out
