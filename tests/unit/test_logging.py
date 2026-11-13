import json

import pytest
import structlog

from xcopilot.config import load_settings
from xcopilot.logging import mask_secrets, setup_logging

SECRETS = {
    "BOT_TOKEN": "123:super-bot-token",
    "ANTHROPIC_API_KEY": "sk-ant-secret-1",
    "DEEPSEEK_API_KEY": "sk-ds-secret-2",
    "TYPESAFE_API_KEY": "ts-secret-3",
    "SECRETS_KEY": "fernet-secret-4",
}


def test_secret_values_masked_in_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    load_settings()
    setup_logging("INFO")

    # по имени поля (в том числе во вложенных словарях)
    masked = mask_secrets({"event": "x", "api_key": "abc", "nested": {"bot_token": "abc"}})
    assert masked["api_key"] == "***"
    assert masked["nested"]["bot_token"] == "***"

    # по значению: секрет из Settings в свободном тексте
    structlog.get_logger().info("calling " + " ".join(SECRETS.values()), extra_field=SECRETS)
    out = capsys.readouterr().out
    for value in SECRETS.values():
        assert value not in out
    assert "***" in out


def test_log_line_is_valid_json_with_request_id(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-42")
    structlog.get_logger().info("hello", foo=1)
    structlog.contextvars.clear_contextvars()

    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["event"] == "hello"
    assert data["request_id"] == "req-42"
    assert data["foo"] == 1
    assert data["level"] == "info"
    assert "timestamp" in data
