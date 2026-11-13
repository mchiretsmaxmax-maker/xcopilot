"""Точка входа сервиса."""

from __future__ import annotations

import asyncio
import signal
import sys

import structlog

from xcopilot.config import load_settings
from xcopilot.errors import ConfigError
from xcopilot.logging import bind_request_id, setup_logging


async def serve(stop: asyncio.Event) -> None:
    """Заглушка основного цикла: ждёт сигнал остановки."""
    await stop.wait()


async def _main() -> None:
    """Создать событие остановки, повесить обработчики сигналов и запустить serve."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await serve(stop)


def run() -> None:
    """Загрузить настройки, настроить логи и запустить сервис."""
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    setup_logging(settings.log_level)
    bind_request_id("startup")
    log = structlog.get_logger()
    log.info("service_started", env=settings.env, tz=settings.default_tz)
    asyncio.run(_main())
    log.info("service_stopped")


if __name__ == "__main__":
    run()
