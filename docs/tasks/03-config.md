## Файлы
- src/xcopilot/config.py

## Новые
- (нет)

## Контекст
- tests/unit/test_config.py
- src/xcopilot/errors.py
- src/xcopilot/logging.py

## Задача
Полностью замени `src/xcopilot/config.py` (сейчас заглушка). pydantic 2.14 + pydantic-settings 2.16, Python 3.13, mypy --strict должен проходить (плагин pydantic.mypy включён). Docstrings на русском.

1. `class Settings(BaseSettings)`, `model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)` — БЕЗ `env_file` (читает только переменные окружения). Поля (атрибуты строчными, имя переменной = имя поля в верхнем регистре):
   - `env: str = "dev"`
   - `bot_token: str` — обязательное, `min_length=1`
   - `owner_tg_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)`
   - `database_url: str` — обязательное, `min_length=1`
   - `secrets_key: str = ""`, `anthropic_api_key: str = ""`, `deepseek_api_key: str = ""`, `typesafe_api_key: str = ""`
   - `default_tz: str = "Europe/Minsk"`
   - `llm_daily_limit_usd: float = 5.0`, `x_daily_limit_usd: float = 1.0` — оба `gt=0`
   - `log_level: str = "INFO"`
2. Валидатор `owner_tg_ids` (`field_validator(..., mode="before")`, classmethod): строка — разделить по запятым, обрезать пробелы, пустые части пропустить, каждую часть `int(...)`; пустая строка → `[]`; нечисловое значение → `ValueError` с текстом вида `OWNER_TG_IDS: ожидаются целые числа через запятую`; уже список — вернуть как есть.
3. Валидатор `default_tz`: `zoneinfo.ZoneInfo(value)` внутри try; при `ZoneInfoNotFoundError`/`ValueError`/`KeyError` → `ValueError(f"неизвестный часовой пояс: {value}")`; вернуть value.
4. Валидатор `log_level`: приведи к верхнему регистру; допустимы DEBUG, INFO, WARNING, ERROR, CRITICAL, иначе `ValueError`.
5. Свойство-метод не нужно. Добавь метод `secret_values(self) -> list[str]`: значения непустых строковых полей, чьё имя оканчивается на `_key` или `_token`.
6. `def load_settings() -> Settings:` создаёт `Settings()`. Ловит `pydantic.ValidationError` и поднимает `ConfigError` (из `xcopilot.errors`) `from e`. Текст ошибки: по одной строке на каждую ошибку из `e.errors()`: `"{ИМЯ_ПЕРЕМЕННОЙ_ВЕРХНИМ}: {msg}"`, где имя берётся из `err["loc"][0]` (приведи к `str(...).upper()`), строки соединены `"; "`. Текст НЕ должен содержать значения (`input`) переменных — секреты не должны попасть в ошибку. После успешного создания вызови `register_secrets(settings.secret_values())` (импорт `from xcopilot.logging import register_secrets`) и верни settings.
7. Тесты `tests/unit/test_config.py` должны проходить (`test_settings_*`). Не трогай другие файлы.

## Коммит
feat: Settings и load_settings с понятными ошибками
