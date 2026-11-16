"""fix/bot-ux-progress: /status — что делает бот (очередь, сбои, действия, расход)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.integration.bot.conftest import Client, make_onboarded

SF = async_sessionmaker[AsyncSession]


async def _job(
    s: AsyncSession, acc: object, kind: str, status: str, finished: str | None = None,
    error: str | None = None,
) -> None:
    await s.execute(
        text(
            "INSERT INTO jobs (account_id, kind, payload, status, run_at, finished_at, last_error)"
            " VALUES (:a, :k, '{}', :st, :r, :f, :e)"
        ),
        {
            "a": acc,
            "k": kind,
            "st": status,
            "r": datetime.fromisoformat("2026-11-13T11:00:00+00"),
            "f": datetime.fromisoformat(finished) if finished else None,
            "e": error,
        },
    )


async def test_status_shows_work_queue_failures_actions_and_spend(
    owner: Client, session_factory: SF, clock: FakeClock
) -> None:
    acc = await make_onboarded(session_factory)
    async with session_factory() as s:
        await _job(s, acc, "writer.generate", "running")
        await _job(s, acc, "writer.topics", "queued")
        await _job(s, acc, "writer.topics", "queued")
        await _job(s, acc, "ingest.process", "queued")
        await _job(
            s, acc, "writer.generate", "dead", "2026-11-13T10:00:00+00",
            "PermanentProviderError: claude_cli: cli_not_found",
        )
        await _job(s, acc, "quality.gate", "done", "2026-11-13T09:00:00+00")
        await s.execute(
            text(
                "INSERT INTO llm_calls (account_id, ts, role, provider, model, purpose, "
                "cost_micro_usd, status) VALUES (:a, :ts, 'judge', 'deepseek', 'm', 'p', "
                "250000, 'ok')"
            ),
            {"a": acc, "ts": datetime.fromisoformat("2026-11-13T08:00:00+00")},
        )
        await s.commit()
    body = (await owner.say("/status"))[-1].text
    assert "В работе" in body and "Пишу посты" in body
    assert "В очереди" in body and "Темы: 2" in body and "Разбор материалов: 1" in body
    assert "Сорвалось за сутки" in body and "Claude CLI не найден" in body
    assert "Последние действия" in body and "Проверка качества" in body
    assert "judge $0.25" in body


async def test_status_button_and_stranger(owner: Client, stranger: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    body = (await owner.say("/status"))[-1].text
    assert "Сорвалось" in body  # пустые разделы показываются, а не пропадают
    other = await stranger.say("/status")
    assert not other or "В работе" not in other[-1].text
