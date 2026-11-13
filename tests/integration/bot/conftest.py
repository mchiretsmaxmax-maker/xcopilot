"""Фикстуры бота: диспетчер на настоящей БД и подделке Telegram, клиент-«пользователь»."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.types import Update
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.telegram import SentMessage, callback_update, message_update
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.config import Settings
from xcopilot.db.repos import AccountsRepo, UsersRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue

OWNER = 100
EDITOR = 200
STRANGER = 999

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def deps(session_factory: SessionFactory, clock: FakeClock, telegram: FakeTelegram) -> BotDeps:
    settings = Settings(
        bot_token="123456:FAKE-TOKEN-for-tests",
        database_url="postgresql+asyncpg://unused",
        owner_tg_ids=[OWNER],
    )

    def uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    return BotDeps(
        bot=telegram.bot,
        settings=settings,
        uow_factory=uow,
        clock=clock,
        queue=JobQueue(uow, clock),
    )


@pytest.fixture
def make_dp(deps: BotDeps) -> Callable[[], Dispatcher]:
    """Свежий диспетчер (как после рестарта процесса): новое хранилище поверх той же БД."""
    return lambda: build_dispatcher(deps)


@pytest.fixture
def dp(make_dp: Callable[[], Dispatcher]) -> Dispatcher:
    return make_dp()


_ids = itertools.count(1)


class Client:
    """Пользователь Telegram: говорит с диспетчером, видит ответы FakeTelegram."""

    def __init__(self, dp: Dispatcher, tg: FakeTelegram, user_id: int) -> None:
        self.dp, self.tg, self.user_id = dp, tg, user_id

    async def feed(self, update: Update) -> list[SentMessage]:
        before = len(self.tg.sent)
        await self.dp.feed_update(self.tg.bot, update)
        return self.tg.sent[before:]

    async def say(self, text: str) -> list[SentMessage]:
        return await self.feed(message_update(next(_ids), self.user_id, text))

    async def press_data(self, data: str) -> list[SentMessage]:
        return await self.feed(callback_update(next(_ids), self.user_id, data))

    async def press(self, label: str) -> list[SentMessage]:
        """Нажать кнопку по подписи на последнем сообщении, где она есть."""
        for msg in reversed(self.tg.sent):
            if msg.chat_id == self.user_id and label in msg.buttons:
                return await self.press_data(msg.callbacks[msg.buttons.index(label)])
        raise AssertionError(f"кнопки {label!r} нет в сообщениях бота")

    @property
    def last(self) -> SentMessage:
        return self.tg.last_to(self.user_id)

    def _ctx(self) -> FSMContext:
        return self.dp.fsm.get_context(self.tg.bot, chat_id=self.user_id, user_id=self.user_id)

    async def state(self) -> str | None:
        state: str | None = await self._ctx().get_state()
        return state

    async def data(self) -> dict[str, Any]:
        data: dict[str, Any] = await self._ctx().get_data()
        return data


@pytest.fixture
def owner(dp: Dispatcher, telegram: FakeTelegram) -> Client:
    return Client(dp, telegram, OWNER)


@pytest.fixture
def stranger(dp: Dispatcher, telegram: FakeTelegram) -> Client:
    return Client(dp, telegram, STRANGER)


async def make_onboarded(
    session_factory: SessionFactory, tg_id: int = OWNER, handle: str = "acc", role: str = "owner"
) -> UUID:
    """Пользователь tg_id с аккаунтом handle (как после онбординга); вернуть id аккаунта."""
    async with UnitOfWork(session_factory) as uow:
        user, _ = await UsersRepo(uow.session).get_or_create(tg_id, is_admin=tg_id == OWNER)
        repo = AccountsRepo(uow.session)
        acc = await repo.get_by_handle(handle)
        if acc is None:
            acc = await repo.create(handle=handle, niche="Крипта", lang="ru")
        await repo.add_member(acc.id, user.id, role=role)
        return acc.id


async def count(session_factory: SessionFactory, table: str, where: str = "true") -> int:
    async with session_factory() as s:
        row = await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"))
        return int(row.scalar_one())
