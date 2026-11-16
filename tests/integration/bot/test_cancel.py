"""fix/bot-control: кнопки «❌ Отменить» в /status и на статус-сообщении прогресса."""

from __future__ import annotations

from uuid import UUID

from aiogram import Dispatcher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram
from tests.integration.bot.conftest import EDITOR, Client, make_onboarded
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.bot.progress import ProgressBoard

SF = async_sessionmaker[AsyncSession]


async def _job(sf: SF, acc: UUID, kind: str, status: str = "queued", payload: str = "{}") -> UUID:
    async with sf() as s:
        row = await s.execute(
            text(
                "INSERT INTO jobs (account_id, kind, payload, status, run_at)"
                " VALUES (:a, :k, CAST(:p AS jsonb), :st, now()) RETURNING id"
            ),
            {"a": acc, "k": kind, "p": payload, "st": status},
        )
        await s.commit()
        return UUID(str(row.scalar_one()))


async def _status(sf: SF, job_id: UUID) -> str:
    async with sf() as s:
        return str(
            (
                await s.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": job_id})
            ).scalar_one()
        )


def _cancel_callbacks(client: Client) -> list[str]:
    return [c for c in client.last.callbacks if c.startswith("jcancel:")]


async def test_status_has_cancel_button_per_job_and_cancel_all(
    owner: Client, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    queued = await _job(session_factory, acc, "writer.topics")
    running = await _job(session_factory, acc, "writer.generate", "running")
    await owner.say("/status")
    callbacks = _cancel_callbacks(owner)
    assert f"jcancel:{queued}" in callbacks and f"jcancel:{running}" in callbacks
    assert "jcancel:all" in callbacks
    assert any("Отменить все" in b for b in owner.last.buttons)
    assert all(b.startswith("❌") for b in owner.last.buttons)


async def test_status_without_jobs_has_no_cancel_buttons(
    owner: Client, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say("/status")
    assert _cancel_callbacks(owner) == []


async def test_cancel_one_job_from_status(owner: Client, session_factory: SF) -> None:
    acc = await make_onboarded(session_factory)
    topics = await _job(session_factory, acc, "writer.topics")
    other = await _job(session_factory, acc, "ingest.process")
    await owner.say("/status")
    await owner.press_data(f"jcancel:{topics}")
    assert await _status(session_factory, topics) == "cancelled"
    assert await _status(session_factory, other) == "queued"
    await owner.press_data(f"jcancel:{topics}")  # повторное нажатие безопасно
    assert await _status(session_factory, topics) == "cancelled"


async def test_cancel_all_from_status_cancels_running_too(
    owner: Client, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    ids = [
        await _job(session_factory, acc, "writer.topics"),
        await _job(session_factory, acc, "writer.generate", "running"),
    ]
    await owner.say("/status")
    await owner.press_data("jcancel:all")
    assert [await _status(session_factory, i) for i in ids] == ["cancelled", "cancelled"]
    body = (await owner.say("/status"))[-1].text
    assert "В работе: ничего" in body and "В очереди: пусто" in body


async def test_editor_cannot_cancel(
    owner: Client, dp: Dispatcher, telegram: FakeTelegram, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    await make_onboarded(session_factory, tg_id=EDITOR, role="editor")
    job = await _job(session_factory, acc, "writer.topics")
    editor = Client(dp, telegram, EDITOR)
    await editor.press_data(f"jcancel:{job}")
    await editor.press_data("jcancel:all")
    assert await _status(session_factory, job) == "queued"
    shown = [a.text for a in telegram.callback_answers()] + telegram.texts_to(EDITOR)
    assert texts.CANCEL_FORBIDDEN in shown


async def test_cannot_cancel_job_of_another_account(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    async with session_factory() as s:
        other = (
            await s.execute(
                text("INSERT INTO accounts (handle, kind) VALUES ('zz', 'user') RETURNING id")
            )
        ).scalar_one()
        await s.commit()
    foreign = await _job(session_factory, other, "writer.topics")
    await owner.press_data(f"jcancel:{foreign}")
    assert await _status(session_factory, foreign) == "queued"


async def test_progress_message_has_cancel_button_and_cancels_flow(
    owner: Client, deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    board = ProgressBoard(Notifier(telegram.bot, deps.uow_factory), clock)
    deps.progress = board
    key = await board.begin(acc, "post")
    assert key is not None
    status_msg = telegram.last_to(100)
    assert f"pcancel:{key}" in status_msg.callbacks
    assert any("Отменить" in b for b in status_msg.buttons)

    await board.step(key, "write")  # правки статуса не теряют кнопку
    assert telegram.edits()[-1].reply_markup is not None

    gen = await _job(session_factory, acc, "writer.generate", "running", f'{{"progress": "{key}"}}')
    gate = await _job(session_factory, acc, "quality.gate", "queued", f'{{"progress": "{key}"}}')
    await owner.press_data(f"pcancel:{key}")
    assert await _status(session_factory, gen) == "cancelled"
    assert await _status(session_factory, gate) == "cancelled"
    last = telegram.edits()[-1]
    assert "⛔ Отменено" in (last.text or "")
    assert last.reply_markup is None  # кнопка убрана


async def test_board_cancelled_is_idempotent_and_ignores_unknown_key(
    deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    board = ProgressBoard(Notifier(telegram.bot, deps.uow_factory), clock)
    key = await board.begin(acc, "topics")
    await board.cancelled(key)
    await board.cancelled(key)
    await board.cancelled("нет такого")
    await board.cancelled(None)
    assert [e.text for e in telegram.edits()].count(telegram.edits()[-1].text) == 1
    assert "⛔ Отменено" in (telegram.edits()[-1].text or "")
