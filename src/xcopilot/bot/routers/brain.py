"""«Мозг» в Telegram (этап 9C): /brain, сегменты, карточки, действия владельца, сводка, откат."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    return Router(name="brain")
