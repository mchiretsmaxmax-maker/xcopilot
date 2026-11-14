"""«🎨 Стиль»: паспорт, правила, примеры, история версий, пересборка, экспорт .md."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="style")
    return router
