"""Eval: устойчивость паспорта на настоящей модели (`pytest -m eval`, ночью; в CI не идёт)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.style_stand import LOWER_POSTS, new_account, seed_chunks
from xcopilot.config import load_settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.factory import build_gateway
from xcopilot.style.extractor import StyleExtractor

SF = async_sessionmaker[AsyncSession]


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = {x.lower() for x in a}, {x.lower() for x in b}
    return len(sa & sb) / max(1, min(len(sa), len(sb)))


@pytest.mark.eval
async def test_eval_passport_stability(session_factory: SF) -> None:
    """Два запуска на одном наборе: жёсткие правила совпадают, лексикон пересекается ≥ 60%."""
    settings = load_settings()
    acc = await new_account(session_factory)
    await seed_chunks(
        session_factory,
        acc,
        LOWER_POSTS + [p + " ещё" for p in LOWER_POSTS] + [p + " снова" for p in LOWER_POSTS],
    )

    def uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    gateway = build_gateway(settings, uow, FakeClock())
    extractor = StyleExtractor(uow, gateway)
    first = await extractor.extract(acc)
    second = await extractor.extract(acc)
    assert first.hard_rules == second.hard_rules
    assert _overlap(first.lexicon.use, second.lexicon.use) >= 0.6
