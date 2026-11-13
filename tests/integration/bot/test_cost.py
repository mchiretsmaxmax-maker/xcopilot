"""/cost: расход за сегодня и месяц по ролям и по X, остаток лимита."""

from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.integration.bot.conftest import Client, make_onboarded

SessionFactory = async_sessionmaker[AsyncSession]


async def _llm(s: AsyncSession, acc: object, ts: str, role: str, micro: int) -> None:
    await s.execute(
        text(
            "INSERT INTO llm_calls (account_id, ts, role, provider, model, purpose, "
            "cost_micro_usd, status) VALUES (:a, :ts, :r, 'anthropic', 'm', 'p', :c, 'ok')"
        ),
        {"a": acc, "ts": datetime.fromisoformat(ts), "r": role, "c": micro},
    )


async def test_cost_command_shows_spend_by_role(
    owner: Client, session_factory: SessionFactory, clock: FakeClock
) -> None:
    acc = await make_onboarded(session_factory)
    clock.advance(14 * 86400 + 12 * 3600)  # 2026-01-15 12:00 UTC
    async with session_factory() as s:
        await _llm(s, acc, "2026-01-15T09:00:00+00", "writer", 1_500_000)
        await _llm(s, acc, "2026-01-15T10:00:00+00", "judge", 500_000)
        await _llm(s, acc, "2026-01-05T10:00:00+00", "writer", 1_000_000)  # только месяц
        await _llm(s, acc, "2025-12-20T10:00:00+00", "writer", 9_000_000)  # другой месяц
        await s.execute(
            text(
                "INSERT INTO x_api_calls "
                "(account_id, ts, endpoint, cost_micro_usd, utc_day, status)"
                " VALUES (:a, :ts, 'GET /2/x', 200000, :day, 'ok')"
            ),
            {"a": acc, "ts": datetime(2026, 1, 15, 9, 30, tzinfo=UTC), "day": date(2026, 1, 15)},
        )
        await s.execute(
            text(
                "INSERT INTO budgets (account_id, scope, period, limit_micro_usd) "
                "VALUES (:a, 'llm', 'day', 5000000)"
            ),
            {"a": acc},
        )
        await s.commit()

    sent = await owner.say("/cost")
    body = sent[-1].text
    today, month = body.split("Месяц")
    assert "Сегодня" in today
    assert "writer $1.50" in today
    assert "judge $0.50" in today
    assert "X: $0.20" in today
    assert "$3.00 из $5.00" in today  # остаток дневного лимита моделей
    assert "writer $2.50" in month
    assert "judge $0.50" in month
    assert "9.00" not in body


async def test_cost_uses_default_limit_and_zero_spend(
    owner: Client, session_factory: SessionFactory
) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/cost")
    assert "$5.00 из $5.00" in sent[-1].text
    assert "X: $0.00" in sent[-1].text
