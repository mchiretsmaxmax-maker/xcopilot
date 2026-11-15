"""Точка входа сервиса."""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

from xcopilot.app import ServiceFailed, build_app, serve_app
from xcopilot.config import Settings, load_settings
from xcopilot.errors import ConfigError
from xcopilot.logging import bind_request_id, setup_logging

_settings: Settings | None = None  # настройки процесса: run() → _main() → serve()


async def serve(stop: asyncio.Event, settings: Settings | None = None) -> None:
    """Собрать приложение и работать до stop (бот + воркер + планировщик).

    Без настроек (тесты каркаса) — только ждать stop: ничего не собирается.
    """
    if stop.is_set():
        return
    if settings is None:
        await stop.wait()
        return
    await serve_app(build_app(settings), stop)


async def _main() -> None:
    """Создать событие остановки, повесить обработчики сигналов и запустить serve."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await serve(stop, _settings)


def run() -> None:
    """Загрузить настройки, настроить логи и запустить сервис."""
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    global _settings
    _settings = settings
    setup_logging(settings.log_level)
    bind_request_id("startup")
    log = structlog.get_logger()
    log.info("service_started", env=settings.env, tz=settings.default_tz)
    try:
        asyncio.run(_main())
    except ServiceFailed as e:
        log.error("service_failed", components=str(e))
        print(f"Сервис остановлен из-за ошибки: {e} (подробности — в логе выше)", file=sys.stderr)
        raise SystemExit(1) from e
    finally:
        _settings = None
    log.info("service_stopped")


if __name__ == "__main__":
    run()
