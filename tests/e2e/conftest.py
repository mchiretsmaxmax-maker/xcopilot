"""Фикстуры сквозных тестов этапа 15."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from tests.e2e.mvp_stand import MvpStand, make_mvp_stand
from tests.fakes import FakeTelegram
from xcopilot.metrics.events import clear_listeners


@pytest.fixture(autouse=True)
def _no_listeners() -> Iterator[None]:
    yield
    clear_listeners()


@pytest.fixture
async def mvp(db_url: str, telegram: FakeTelegram, tmp_path: Path) -> AsyncIterator[MvpStand]:
    stand = await make_mvp_stand(db_url, telegram, tmp_path)
    try:
        yield stand
    finally:
        await stand.app.aclose()
