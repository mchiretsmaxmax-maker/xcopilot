import pytest

from xcopilot.healthcheck import main


def test_healthcheck_ok_with_db(monkeypatch: pytest.MonkeyPatch, postgres_url: str) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:t")
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    assert main() == 0


def test_healthcheck_fails_on_bad_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    assert main() == 1
