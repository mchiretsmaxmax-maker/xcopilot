"""Перезапуск бота по кнопке (fix/bot-control): метка перед остановкой и сообщение после старта.

Владелец жмёт «🔄 Перезапустить бота»: бот пишет метку `restart.requested` (у системного
аккаунта global, payload — Telegram-ID нажавшего) и останавливает процесс с кодом выхода 3.
`scripts/run_local.sh` (локально) или `restart: unless-stopped` (compose) поднимают его снова;
при старте `announce_restart` снимает метку и пишет «✅ Бот перезапущен».
"""

from __future__ import annotations

from collections.abc import Callable

import structlog
from aiogram import Bot

from xcopilot.bot import texts
from xcopilot.clock import Clock
from xcopilot.db.repos import OpsRepo
from xcopilot.db.uow import UnitOfWork

log = structlog.get_logger()

RESTART_KEY = "restart.requested"


async def mark_restart(uow_factory: Callable[[], UnitOfWork], clock: Clock, tg_id: int) -> None:
    """Запомнить, кто просил перезапуск (сообщение после старта уйдёт ему)."""
    async with uow_factory() as uow:
        repo = OpsRepo(uow.session)
        await repo.set_mark(
            await repo.global_account_id(), RESTART_KEY, clock.now(), {"tg_id": tg_id}
        )


async def announce_restart(bot: Bot, uow_factory: Callable[[], UnitOfWork]) -> int:
    """После старта: снять метку и сообщить «✅ Бот перезапущен»; вернуть число сообщений."""
    async with uow_factory() as uow:
        repo = OpsRepo(uow.session)
        payload = await repo.take_mark(await repo.global_account_id(), RESTART_KEY)
    if not payload or "tg_id" not in payload:
        return 0
    try:
        await bot.send_message(int(payload["tg_id"]), texts.RESTARTED)
    except Exception:
        log.warning("restart_announce_failed", exc_info=True)
        return 0
    return 1
