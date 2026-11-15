"""E2E: «✍️ Написать», «💡 Темы», «🎯 Хвост тренда» через диспетчер бота + задачи очереди."""

from __future__ import annotations

from uuid import UUID

import pytest
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import OWNER, Client, make_onboarded
from tests.writer_stand import DISTINCT, WriterStand, draft_reply, make_writer_stand, topics_reply
from xcopilot.bot import texts
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.jobs.queue import Job
from xcopilot.writer.jobs import (
    WRITER_GENERATE_KIND,
    WRITER_TOPICS_KIND,
    handle_writer_generate,
    handle_writer_topics,
    process_writer_job,
)

SF = async_sessionmaker[AsyncSession]
ORIGINAL = "За 30 дней я потерял 12% на мостах. Один вывод: держи деньги там, где живёшь."


class Rig:
    def __init__(self, ws: WriterStand, owner: Client, deps: BotDeps, tg: FakeTelegram) -> None:
        self.ws, self.owner, self.deps, self.tg = ws, owner, deps, tg

    async def next_job(self, kind: str) -> Job:
        jobs = await self.deps.queue.claim("w", 10)
        assert [j.kind for j in jobs] == [kind]
        return jobs[0]

    async def run_generate(self) -> None:
        job = await self.next_job(WRITER_GENERATE_KIND)
        notifier = Notifier(self.deps.bot, self.deps.uow_factory)

        async def handler(j: Job) -> object:
            return await handle_writer_generate(j, writer=self.ws.service, notifier=notifier)

        await process_writer_job(self.deps.queue, job, handler)


@pytest.fixture
async def rig(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> Rig:
    ws = await make_writer_stand(session_factory, handle="acc")
    acc = await make_onboarded(session_factory)
    assert acc == ws.account_id
    deps.queue = ws.queue
    dp: Dispatcher = build_dispatcher(deps)
    return Rig(ws, Client(dp, telegram, OWNER), deps, telegram)


def _texts(rig: Rig) -> list[str]:
    return [m.text or "" for m in rig.tg.sent if m.chat_id == OWNER]


async def test_write_post_by_typed_topic_sends_three_cards(rig: Rig) -> None:
    await rig.owner.press_data("menu:write")
    assert rig.owner.last.text == texts.WRITE_CHOOSE_FORMAT
    await rig.owner.press("Пост")
    assert rig.owner.last.text == texts.WRITE_ASK_TOPIC_FREE
    await rig.owner.say("да")
    assert rig.owner.last.text == texts.WRITE_TOPIC_TOO_SHORT
    await rig.owner.say("газ по выходным дешевле")
    assert rig.owner.last.text == texts.WRITING
    assert await rig.owner.state() is None

    rig.ws.writer.push(draft_reply(*DISTINCT))
    await rig.run_generate()

    cards = [t for t in _texts(rig) if t.startswith("Вариант ")]
    assert len(cards) == 3 and DISTINCT[0] in cards[0]
    assert "газ по выходным дешевле" in rig.ws.prompt()


async def test_write_reply_asks_target_then_point(rig: Rig) -> None:
    await rig.owner.press_data("menu:write")
    await rig.owner.press("Ответ")
    assert rig.owner.last.text == texts.WRITE_ASK_TARGET
    await rig.owner.say("https://x.com/nobody/status/999")
    assert rig.owner.last.text == texts.WRITE_TARGET_NOT_FOUND
    await rig.owner.say("Роллапы скоро умрут, вот увидите")
    assert rig.owner.last.text == texts.WRITE_ASK_REPLY_POINT
    await rig.owner.say("-")
    assert rig.owner.last.text == texts.WRITING

    rig.ws.writer.push(draft_reply(*DISTINCT))
    await rig.run_generate()

    prompt = rig.ws.prompt()
    assert "Роллапы скоро умрут, вот увидите" in prompt and "<untrusted" in prompt


async def test_topics_flow_write_and_drop(rig: Rig) -> None:
    await rig.owner.press_data("menu:topics")
    assert rig.owner.last.text == texts.TOPICS_SEARCHING
    job = await rig.next_job(WRITER_TOPICS_KIND)
    rig.ws.writer.push(
        topics_reply("мосты закрываются тихо", "газ по выходным", "апгрейд и паника")
    )
    notifier = Notifier(rig.deps.bot, rig.deps.uow_factory)
    found = await handle_writer_topics(job, topics=rig.ws.topics, notifier=notifier)
    assert len(found) == 3
    assert rig.owner.last.buttons == ["Писать", "Не то"]

    await rig.owner.press("Не то")
    assert rig.owner.last.text == texts.TOPIC_DROPPED
    rows = await rig.ws.rows("SELECT status FROM topics WHERE id = :i", i=found[-1].id)
    assert rows == [("dropped",)]

    await rig.owner.press_data(f"wtopic:{found[0].id}:post")
    assert rig.owner.last.text == texts.WRITING
    job = await rig.next_job(WRITER_GENERATE_KIND)
    assert job.payload["topic_id"] == str(found[0].id)
    assert job.payload["task"]["topic"].startswith("мосты закрываются тихо")


async def test_write_pick_topic_from_list(rig: Rig) -> None:
    topic = await rig.ws.topics.add_manual_topic(rig.ws.account_id, "мой разбор апгрейда")
    await rig.owner.press_data("menu:write")
    await rig.owner.press("Тред")
    assert rig.owner.last.text == texts.WRITE_ASK_TOPIC
    await rig.owner.press("мой разбор апгрейда")
    assert rig.owner.last.text == texts.WRITING
    job = await rig.next_job(WRITER_GENERATE_KIND)
    assert job.payload["task"]["format"] == "thread"
    assert job.payload["topic_id"] == str(topic.id)
    rows = await rig.ws.rows("SELECT status FROM topics WHERE id = :i", i=topic.id)
    assert rows == [("in_progress",)]


async def test_topic_for_reply_asks_target(rig: Rig) -> None:
    topic = await rig.ws.topics.add_manual_topic(rig.ws.account_id, "ответ про мосты")
    await rig.owner.press_data(f"wtopic:{topic.id}:reply")
    assert rig.owner.last.text == texts.WRITE_ASK_TARGET
    await rig.owner.press_data("wtopic:not-a-uuid:post")
    assert rig.owner.last.text == texts.TOPIC_NOT_FOUND
    await rig.owner.press_data("wdrop:not-a-uuid")
    assert rig.owner.last.text == texts.TOPIC_NOT_FOUND
    await rig.owner.press_data(f"wtopic:{UUID(int=1)}:post")
    assert rig.owner.last.text == texts.TOPIC_NOT_FOUND


async def test_trend_tail_shows_hypothesis_and_cards(rig: Rig) -> None:
    source = await rig.ws.foreign_post(ORIGINAL, url="https://x.com/someone/status/777")
    async with rig.ws.sf() as s:
        from sqlalchemy import text

        await s.execute(text("UPDATE sources SET external_id = '777' WHERE id = :i"), {"i": source})
        await s.commit()

    await rig.owner.press_data("menu:trend")
    assert rig.owner.last.text == texts.TREND_ASK_POST
    await rig.owner.say("https://x.com/someone/status/777")
    sent = _texts(rig)
    assert texts.trend_why("короткий зачин с цифрой цепляет") in sent
    assert rig.owner.last.text == texts.WRITING

    rig.ws.writer.push(draft_reply(*DISTINCT))
    await rig.run_generate()

    sent = _texts(rig)
    assert sum(t.startswith("Вариант ") for t in sent) == 3
    assert sent.count(texts.trend_why("короткий зачин с цифрой цепляет")) == 2


async def test_trend_tail_plain_text_without_analysis(rig: Rig) -> None:
    await rig.owner.press_data("menu:trend")
    await rig.owner.say(ORIGINAL)
    assert texts.TREND_NO_ANALYSIS in _texts(rig)
    job = await rig.next_job(WRITER_GENERATE_KIND)
    assert job.payload["mode"] == "trend_tail"


async def test_writer_sections_need_account(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    owner = Client(build_dispatcher(deps), telegram, OWNER)
    for section in ("write", "topics", "trend"):
        await owner.press_data(f"menu:{section}")
        assert owner.last.text == texts.NO_ACCOUNT
