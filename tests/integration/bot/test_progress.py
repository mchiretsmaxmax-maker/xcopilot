"""fix/bot-ux-progress: одно статус-сообщение правится по этапам; ошибка видна."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram
from tests.integration.bot.conftest import Client, make_onboarded
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.failures import DeadJobReporter
from xcopilot.bot.notify import Notifier
from xcopilot.bot.progress import ProgressBoard
from xcopilot.jobs.queue import Job
from xcopilot.llm.errors import PermanentProviderError
from xcopilot.writer.jobs import handle_writer_topics
from xcopilot.writer.schemas import Topic

SF = async_sessionmaker[AsyncSession]


def _board(deps: BotDeps, telegram: FakeTelegram, clock: FakeClock) -> ProgressBoard:
    return ProgressBoard(Notifier(telegram.bot, deps.uow_factory), clock)


def _edits(telegram: FakeTelegram) -> list[str]:
    return [c.text for c in telegram.calls if isinstance(c, EditMessageText)]


async def test_status_message_is_edited_stage_by_stage(
    deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    board = _board(deps, telegram, clock)
    key = await board.begin(acc, "post")
    assert key is not None
    first = telegram.last_to(100)
    clock.advance(5)
    await board.step(key, "write")
    await board.step(key, "write")  # то же самое — без лишней правки
    await board.step(key, "gate")
    await board.done(key)
    edits = _edits(telegram)
    assert len(edits) == 3
    assert "✍️ Пишу 3 варианта" in edits[0]
    assert "🛡 Проверяю качество" in edits[1]
    assert edits[-1].rstrip().endswith("✅ Готово, карточки ниже") or "✅ Готово" in edits[-1]
    assert "12:00:05" in edits[0]  # у этапа отметка времени
    assert len([m for m in telegram.sent if m.chat_id == 100]) == 1  # сообщение одно
    assert first.message_id == [c for c in telegram.calls if isinstance(c, EditMessageText)][
        0
    ].message_id


async def test_failure_marks_stage_with_reason(
    deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    board = _board(deps, telegram, clock)
    key = await board.begin(acc, "post")
    assert key is not None
    await board.step(key, "write")
    await board.fail(key, "Claude CLI не найден")
    assert "❌ Пишу 3 варианта: Claude CLI не найден" in _edits(telegram)[-1]


async def test_dead_job_reporter_edits_status_instead_of_second_message(
    deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    board = _board(deps, telegram, clock)
    key = await board.begin(acc, "post")
    await board.step(key, "write")  # type: ignore[arg-type]
    notifier = Notifier(telegram.bot, deps.uow_factory)
    job = Job(
        id=__import__("uuid").uuid4(),
        account_id=acc,
        kind="writer.generate",
        payload={"progress": key},
        status="dead",
        attempts=1,
        max_attempts=5,
        run_at=clock.now(),
        locked_by=None,
        last_error=None,
    )
    await DeadJobReporter(notifier, board)(
        job, PermanentProviderError("claude_cli", kind="cli_not_found")
    )
    assert "❌" in _edits(telegram)[-1] and "Claude CLI не найден" in _edits(telegram)[-1]
    # и всё равно отдельное сообщение: правка не даёт уведомления
    assert "Писатель недоступен" in telegram.last_to(100).text


async def test_edit_not_modified_is_swallowed(
    deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    class Bad(Bot):
        pass

    notifier = Notifier(telegram.bot, deps.uow_factory)

    async def raising(*a: Any, **k: Any) -> Any:
        raise TelegramBadRequest(
            EditMessageText(chat_id=1, message_id=1, text="x"),
            "Bad Request: message is not modified",
        )

    object.__setattr__(telegram.bot, "edit_message_text", raising)
    await notifier.edit_text(1, 1, "x")  # не падает


async def test_topics_handler_publishes_stages(
    deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    acc = await make_onboarded(session_factory)
    board = _board(deps, telegram, clock)
    key = await board.begin(acc, "topics")

    class FakeTopics:
        async def suggest_topics(self, account_id: object, n: int = 5) -> list[Topic]:
            return []

    job = Job(
        id=__import__("uuid").uuid4(),
        account_id=acc,
        kind="writer.topics",
        payload={"n": 5, "progress": key},
        status="running",
        attempts=1,
        max_attempts=5,
        run_at=clock.now(),
        locked_by=None,
        last_error=None,
    )
    await handle_writer_topics(
        job, topics=FakeTopics(), notifier=Notifier(telegram.bot, deps.uow_factory), progress=board  # type: ignore[arg-type]
    )
    edits = _edits(telegram)
    assert "💡 Подбираю темы" in edits[0]
    assert "✅" in edits[-1]


async def test_topics_button_creates_status_message_and_payload(
    owner: Client, deps: BotDeps, telegram: FakeTelegram, clock: FakeClock, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    deps.progress = _board(deps, telegram, clock)
    sent = await owner.say("💡 Темы")
    assert "Задача принята" in sent[-1].text
    async with session_factory() as s:
        from sqlalchemy import text

        payload = (
            await s.execute(text("SELECT payload FROM jobs WHERE kind = 'writer.topics'"))
        ).scalar_one()
    assert payload.get("progress")
