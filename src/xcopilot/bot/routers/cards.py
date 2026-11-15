"""Роутер этапа 12 (cards)."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    return Router(name="cards")
