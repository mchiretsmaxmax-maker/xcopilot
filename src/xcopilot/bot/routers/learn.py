"""Обучение: владелец присылает тексты, ссылки, хэндлы и файлы → ингест."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    router = Router(name="learn")
    return router
