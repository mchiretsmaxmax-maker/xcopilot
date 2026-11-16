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

RESTART_EXIT_CODE = (
    3  # владелец нажал «Перезапустить»: оболочка (run_local.sh, compose) стартует снова
)

_settings: Settings | None = None  # настройки процесса: run() → _main() → serve()


async def serve(
    stop: asyncio.Event, settings: Settings | None = None, restart: asyncio.Event | None = None
) -> None:
    """Собрать приложение и работать до stop (бот + воркер + планировщик).

    Без настроек (тесты каркаса) — только ждать stop: ничего не собирается.
    """
    if stop.is_set():
        return
    if settings is None:
        await stop.wait()
        return
    app = build_app(settings)
    if restart is not None:

        def request_restart() -> None:
            restart.set()
            stop.set()

        app.bot_deps.request_restart = request_restart
    await serve_app(app, stop)


async def _main() -> bool:
    """Создать событие остановки, повесить обработчики сигналов и запустить serve.

    Вернуть True, если остановку запросил владелец кнопкой «Перезапустить».
    """
    stop = asyncio.Event()
    restart = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    await serve(stop, _settings, restart)
    return restart.is_set()


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
    restarted = False
    try:
        restarted = bool(asyncio.run(_main()))
    except ServiceFailed as e:
        log.error("service_failed", components=str(e))
        print(f"Сервис остановлен из-за ошибки: {e} (подробности — в логе выше)", file=sys.stderr)
        raise SystemExit(1) from e
    finally:
        _settings = None
    log.info("service_stopped", restart=restarted)
    if restarted:
        raise SystemExit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    run()
