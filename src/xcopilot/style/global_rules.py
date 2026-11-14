"""Глобальный пакет анти-AI правил v1 (RU и EN) → принятые уроки системного аккаунта."""

from __future__ import annotations

from xcopilot.db.uow import DbSession


async def load_seed(session: DbSession) -> int:
    raise NotImplementedError
