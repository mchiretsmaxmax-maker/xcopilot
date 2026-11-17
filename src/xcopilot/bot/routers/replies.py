"""«💬 Ответы»: режим ответов на посты по ссылке (ADR 0032)."""

from __future__ import annotations

from aiogram import Router


def build_router() -> Router:
    return Router(name="replies")
