"""«Пост → Ответ/Цитата»: ссылки на пост, которого нет в базе, читаются без ключа (ADR 0031).

Раньше путь «Написать» искал пост только в своей базе и просил прислать текст целиком.
"""

from __future__ import annotations

from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import OWNER, Client, make_onboarded
from tests.writer_stand import make_writer_stand
from xcopilot.bot import texts
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.jobs.queue import Job
from xcopilot.x.errors import XNotFound
from xcopilot.x.models import PostRef, XPost

SF = async_sessionmaker[AsyncSession]
POST_ID = "2122669355876159959"
LINK = f"https://x.com/DefiIgnas/status/{POST_ID}?s=20"
POST_TEXT = "TLDR: 9% decline in total stablecoin supply might seem small"


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, ref: PostRef) -> XPost:
        self.calls.append(ref.id)
        if ref.id != POST_ID:
            raise XNotFound(ref.id)
        return XPost(
            id=POST_ID,
            text=POST_TEXT,
            author_handle="DefiIgnas",
            created_at=None,
            source="fxtwitter",
        )

    get_post = fetch


async def _client(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> Client:
    ws = await make_writer_stand(session_factory, handle="acc")
    await make_onboarded(session_factory)
    deps.queue = ws.queue
    dp: Dispatcher = build_dispatcher(deps)
    return Client(dp, telegram, OWNER)


async def test_reply_link_not_in_base_is_read_by_reader(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    reader = FakeReader()
    deps.post_reader = reader
    owner = await _client(session_factory, telegram, deps)
    await owner.press_data("menu:write")
    await owner.press("Ответ")
    await owner.say(LINK)
    assert reader.calls == [POST_ID]
    assert owner.last.text == texts.WRITE_ASK_REPLY_POINT
    await owner.say("-")
    assert owner.last.text == texts.WRITING
    jobs: list[Job] = await deps.queue.claim("w", 10)
    target = jobs[0].payload["target"]
    assert target["text"] == POST_TEXT
    assert target["author"] == "DefiIgnas"
    assert POST_ID in target["url"]


async def test_reply_link_unreadable_still_asks_for_text(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    deps.post_reader = FakeReader()
    owner = await _client(session_factory, telegram, deps)
    await owner.press_data("menu:write")
    await owner.press("Ответ")
    await owner.say("https://x.com/nobody/status/999")
    assert owner.last.text == texts.WRITE_TARGET_NOT_FOUND


async def test_main_menu_replies_button_enters_reply_mode(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    deps.post_reader = FakeReader()
    owner = await _client(session_factory, telegram, deps)
    await owner.press_data("menu:replies")
    assert owner.last.text == texts.REPLY_MODE_ENTER
    assert await owner.state() == "ReplyFlow:link"
