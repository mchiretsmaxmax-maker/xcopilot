import pytest

from xcopilot.config import load_settings
from xcopilot.errors import ConfigError

BASE_ENV = {
    "BOT_TOKEN": "123:bot-token",
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
}
ALL_VARS = [
    "ENV", "BOT_TOKEN", "OWNER_TG_IDS", "DATABASE_URL", "SECRETS_KEY", "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY", "TYPESAFE_API_KEY", "DEFAULT_TZ", "LLM_DAILY_LIMIT_USD",
    "X_DAILY_LIMIT_USD", "LOG_LEVEL",
]  # fmt: skip


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_settings_requires_mandatory_env(env: pytest.MonkeyPatch) -> None:
    env.delenv("BOT_TOKEN")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "BOT_TOKEN" in str(exc.value)


def test_settings_parses_owner_ids(env: pytest.MonkeyPatch) -> None:
    env.setenv("OWNER_TG_IDS", "1, 2")
    assert load_settings().owner_tg_ids == [1, 2]

    env.setenv("OWNER_TG_IDS", "1, abc")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "OWNER_TG_IDS" in str(exc.value)


def test_settings_rejects_invalid_timezone(env: pytest.MonkeyPatch) -> None:
    env.setenv("DEFAULT_TZ", "Mars/Olympus")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "DEFAULT_TZ" in str(exc.value)


@pytest.mark.parametrize("name", ["LLM_DAILY_LIMIT_USD", "X_DAILY_LIMIT_USD"])
def test_settings_limits_must_be_positive(env: pytest.MonkeyPatch, name: str) -> None:
    env.setenv(name, "-1")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert name in str(exc.value)
