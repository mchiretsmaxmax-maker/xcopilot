## Файлы
- src/xcopilot/healthcheck.py

## Новые
- (нет)

## Контекст
- tests/unit/test_healthcheck.py
- src/xcopilot/config.py
- src/xcopilot/errors.py

## Задача
Полностью замени `src/xcopilot/healthcheck.py`. Python 3.13, SQLAlchemy 2.1 async + asyncpg, mypy --strict. Docstrings на русском.

1. `async def check_db(database_url: str, timeout: float = 3.0) -> None`: создай `create_async_engine(database_url)`, в `try` выполни `async with engine.connect() as conn: await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout)`, в `finally` — `await engine.dispose()`. Исключения не глуши.
2. `def main() -> int`: `settings = load_settings()` (из `xcopilot.config`); `try: asyncio.run(check_db(settings.database_url))`; при любом `Exception` (включая `ConfigError`) напечатай одну строку в stderr `healthcheck: <имя класса исключения>` (НЕ печатай текст исключения — в нём может быть пароль) и верни 1; иначе верни 0.
3. В конце файла: `if __name__ == "__main__": raise SystemExit(main())`.
4. Тест `tests/unit/test_healthcheck.py` должен проходить. Импорт модуля не должен иметь побочных эффектов.

## Коммит
feat: healthcheck — код 0, если БД отвечает
