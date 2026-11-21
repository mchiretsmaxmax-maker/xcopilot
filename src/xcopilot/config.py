"""Настройки приложения, читаемые из переменных окружения."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from xcopilot.errors import ConfigError
from xcopilot.logging import register_secrets


class Settings(BaseSettings):
    """Настройки приложения."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    env: str = "dev"
    bot_token: str = Field(min_length=1)
    owner_tg_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    database_url: str = Field(min_length=1)
    secrets_key: str = ""
    deepseek_api_key: str = ""
    typesafe_api_key: str = ""
    codex_cli_path: str = "codex"
    codex_cli_effort: Literal["low", "medium", "high", "xhigh"] = "medium"
    codex_cli_timeout: float = Field(default=180.0, gt=0)
    codex_cli_concurrency: int = Field(default=1, ge=1)
    default_tz: str = "Europe/Minsk"
    llm_daily_limit_usd: float = Field(default=5.0, gt=0)
    x_daily_limit_usd: float = Field(default=1.0, gt=0)
    x_lookup_daily_limit_usd: float = Field(default=0.5, gt=0)
    x_public_read: bool = True  # ADR 0031: чужие публичные посты по ссылке без ключа (fxtwitter)
    x_redirect_uri: str = "http://127.0.0.1:8765/x/callback"
    # fix/deepseek-balance: пустой счёт DeepSeek (402) — задачи ждут, владелец знает заранее
    deepseek_balance_check_hours: float = Field(default=6.0, gt=0)  # как часто /user/balance
    deepseek_balance_min_usd: float = Field(default=1.0, ge=0)  # ниже — предупреждение раз в сутки
    deepseek_cny_per_usd: float = Field(default=7.2, gt=0)  # курс CNY → USD для порога
    deepseek_empty_retry_minutes: float = Field(default=30.0, gt=0)  # задачи при 402 ждут столько
    deepseek_empty_alert_hours: float = Field(default=6.0, gt=0)  # «кончились деньги» не чаще
    heartbeat_url: str = ""  # аудит I: адрес внешнего сторожа; пусто — выключен (секрет)
    heartbeat_interval: float = Field(default=60.0, gt=0)
    log_level: str = "INFO"
    data_dir: Path = Path("data")

    @property
    def ingest_dir(self) -> Path:
        """Временные файлы ингеста (между приёмом и разбором; удаляются после разбора)."""
        return self.data_dir / "ingest"

    @field_validator("owner_tg_ids", mode="before")
    @classmethod
    def parse_owner_tg_ids(cls, value: object) -> object:
        """Разобрать OWNER_TG_IDS из строки с запятыми или вернуть список как есть."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            if not value.strip():
                return []
            parts = [part.strip() for part in value.split(",") if part.strip()]
            try:
                return [int(part) for part in parts]
            except ValueError as exc:
                raise ValueError("OWNER_TG_IDS: ожидаются целые числа через запятую") from exc
        return value

    @field_validator("default_tz")
    @classmethod
    def validate_default_tz(cls, value: str) -> str:
        """Проверить, что DEFAULT_TZ — известный часовой пояс IANA."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ValueError(f"неизвестный часовой пояс: {value}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Привести LOG_LEVEL к верхнему регистру и проверить допустимые значения."""
        upper = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if upper not in allowed:
            raise ValueError(
                "LOG_LEVEL должен быть одним из: DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )
        return upper

    def secret_values(self) -> list[str]:
        """Вернуть непустые значения полей, чьё имя оканчивается на _key или _token."""
        result: list[str] = []
        for name, value in self.model_dump().items():
            if name.endswith(("_key", "_token")) and isinstance(value, str) and value:
                result.append(value)
        if self.heartbeat_url:  # зная адрес, можно слать ложный пульс
            result.append(self.heartbeat_url)
        return result


def load_settings() -> Settings:
    """Создать настройки из переменных окружения и зарегистрировать секреты в логгере."""
    try:
        settings = Settings()
    except ValidationError as e:
        messages = []
        for err in e.errors():
            loc = err.get("loc", ())
            name = str(loc[0]).upper() if loc else "UNKNOWN"
            messages.append(f"{name}: {err['msg']}")
        raise ConfigError("; ".join(messages)) from e
    register_secrets(settings.secret_values())
    return settings
