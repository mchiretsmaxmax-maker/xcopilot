import pytest

from xcopilot.healthcheck import main


def test_healthcheck_fails_without_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:t")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:1/db")
    assert main() != 0
