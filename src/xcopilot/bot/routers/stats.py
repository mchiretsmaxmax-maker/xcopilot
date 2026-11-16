"""/stats и «📊 Аналитика» (этап 14, задача 9). Заглушка."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    return Router(name="stats")
