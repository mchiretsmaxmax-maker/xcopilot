"""Глобальный пакет анти-AI правил v1 → принятые уроки системного аккаунта (задача 9)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from xcopilot.db.uow import UnitOfWork
from xcopilot.style.global_rules import load_seed

SF = async_sessionmaker[AsyncSession]


async def lessons(sf: SF) -> list[tuple[str, str, str]]:
    async with sf() as s:
        rows = await s.execute(
            text(
                "SELECT l.kind, l.status, l.text FROM lessons l JOIN accounts a "
                "ON a.id = l.account_id WHERE a.kind = 'global' ORDER BY l.text"
            )
        )
        return [(r[0], r[1], r[2]) for r in rows]


async def test_global_seed_loaded_once(session_factory: SF) -> None:
    async with UnitOfWork(session_factory) as uow:
        first = await load_seed(uow.session)
    rows = await lessons(session_factory)
    assert first == len(rows) >= 20
    assert {status for _, status, _ in rows} == {"accepted"}
    assert {kind for kind, _, _ in rows} <= {"do", "dont", "pattern"}
    texts = " ".join(t for _, _, t in rows).lower()
    assert "delve" in texts  # EN
    assert "в современном мире" in texts  # RU
    async with UnitOfWork(session_factory) as uow:
        second = await load_seed(uow.session)
    assert second == 0
    assert len(await lessons(session_factory)) == len(rows)
