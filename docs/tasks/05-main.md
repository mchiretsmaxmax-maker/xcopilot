## Файлы
- src/xcopilot/main.py

## Новые
- (нет)

## Контекст
- src/xcopilot/config.py
- src/xcopilot/logging.py
- src/xcopilot/clock.py

## Задача
Полностью замени `src/xcopilot/main.py`. Python 3.13, mypy --strict, docstrings на русском.

1. `async def serve(stop: asyncio.Event) -> None`: `await stop.wait()` (заглушка цикла; настоящий бот появится на следующих этапах).
2. `def run() -> None`:
   - `settings = load_settings()` (`from xcopilot.config import load_settings`);
   - `setup_logging(settings.log_level)`, `bind_request_id("startup")` (оба из `xcopilot.logging`);
   - `log = structlog.get_logger()`; `log.info("service_started", env=settings.env, tz=settings.default_tz)` — без секретов;
   - `asyncio.run(_main())`, где `async def _main()` создаёт `stop = asyncio.Event()`, регистрирует `loop.add_signal_handler(sig, stop.set)` для `signal.SIGINT` и `signal.SIGTERM`, затем `await serve(stop)`;
   - после выхода: `log.info("service_stopped")`.
   - При `ConfigError` (из `xcopilot.errors`) в `run`: напечатай `print(f"Ошибка конфигурации: {e}", file=sys.stderr)` и `raise SystemExit(2)`.
3. В конце файла: `if __name__ == "__main__": run()`.
4. Импорт модуля не должен иметь побочных эффектов (ничего не выполняется на уровне модуля).

## Коммит
feat: main.run — настройки, логи, заглушка цикла
