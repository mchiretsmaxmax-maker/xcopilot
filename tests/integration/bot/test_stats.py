"""/stats читает metric_values; в режиме off — только продуктовые метрики (этап 14, 9–10)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.integration.bot.conftest import Client, make_onboarded
from xcopilot.metrics.catalog import sync_catalog

SessionFactory = async_sessionmaker[AsyncSession]


async def _value(s: AsyncSession, acc: Any, key: str, day: date, value: float, n: int) -> None:
    await s.execute(
        text(
            "INSERT INTO metric_values (account_id, metric_key, grain, period_start, dims,"
            " dims_hash, value, sample_size, definition_version, computed_at)"
            " VALUES (:a, :k, 'day', :d, '{}'::jsonb, 'h', :v, :n, 1, now())"
        ),
        {"a": acc, "k": key, "d": day, "v": value, "n": n},
    )


async def _seed(sf: SessionFactory, clock: FakeClock, *, mode: str) -> None:
    acc = await make_onboarded(sf)
    clock.advance(14 * 86400 + 12 * 3600)  # 15.01.2026 12:00 UTC
    async with sf() as s:
        await sync_catalog(s)
        await s.execute(
            text("UPDATE accounts SET x_read_mode = :m, tz = 'UTC' WHERE id = :a"),
            {"m": mode, "a": acc},
        )
        for d in (date(2026, 1, 13), date(2026, 1, 14)):
            await _value(s, acc, "posts_published_day", d, 2, 2)
            await _value(s, acc, "impressions_24h_median", d, 4321, 2)
            await _value(s, acc, "approval_rate", d, 0.75, 4)
            await _value(s, acc, "zero_edit_rate", d, 0.5, 4)
        await s.commit()


async def test_stats_command_reads_metric_values(
    owner: Client, session_factory: SessionFactory, clock: FakeClock
) -> None:
    await _seed(session_factory, clock, mode="owned")
    sent = await owner.say("/stats")
    body = sent[-1].text
    assert "4321" in body or "4 321" in body  # показы (медиана)
    assert "75%" in body  # доля одобренных
    assert "Постов опубликовано: 4" in body
    assert {"Что зашло", "Что улучшить", "Цифры"} <= set(sent[-1].buttons)


async def test_mode_off_shows_product_metrics_only(
    owner: Client, session_factory: SessionFactory, clock: FakeClock
) -> None:
    await _seed(session_factory, clock, mode="off")
    body = (await owner.say("/stats"))[-1].text
    assert "75%" in body and "50%" in body  # продуктовые: одобрения и без правок
    assert "4321" not in body and "4 321" not in body  # охватов нет
    assert "показ" not in body.lower()


@pytest.mark.parametrize("data", ["menu:analytics"])
async def test_menu_analytics_opens_stats(
    owner: Client, session_factory: SessionFactory, clock: FakeClock, data: str
) -> None:
    await _seed(session_factory, clock, mode="owned")
    sent = await owner.press_data(data)
    assert "Постов опубликовано" in sent[-1].text
