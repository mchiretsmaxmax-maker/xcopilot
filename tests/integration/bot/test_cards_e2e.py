"""Этап 12: полный проход «тема → три варианта → правка → ссылка → подтверждение», события
решений и то, что бот ничего не пишет в X."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.quality_stand import good
from tests.writer_stand import DISTINCT, draft_reply
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.metrics.rollup import compute
from xcopilot.review.callbacks import APPROVE, EDIT, PUBLISHED, REJECT_REASON
from xcopilot.review.jobs import PUBLICATIONS_MATCH_KIND

SF = async_sessionmaker[AsyncSession]
READ_CALLS = {"get_post", "get_own_posts", "iter_own_history", "get_user"}
EDITED = "блобы снизили комиссии роллапов втрое, и это видно по моим транзакциям за неделю"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


async def test_e2e_topic_to_published(st: CardsStand) -> None:
    owner = st.owner
    await owner.press_data("menu:write")
    await owner.press("Пост")
    st.gs.ws.writer.push(draft_reply(*DISTINCT))
    st.gs.judge.push(good(), good(), good())
    await owner.say("почему комиссии в l2 падают быстрее активности")
    assert owner.last.text == texts.WRITING
    await st.drain()

    [card] = st.cards()
    assert card.text.startswith("📝 Вариант 1/3 · пост")
    assert "Проверка: линтер ✅" in card.text
    run = (await st.rows("SELECT id FROM generation_runs"))[0][0]
    d1 = (await st.finals(run))[0]

    await owner.press("✏️ Править")
    await owner.say(EDITED)
    assert "🚀 Открыть в X" in owner.last.buttons
    await owner.press("✔️ Опубликовал")
    assert owner.last.text == texts.ASK_LINK
    st.own_post("1900000000000000009", EDITED)
    await owner.say("https://x.com/acc/status/1900000000000000009")
    assert owner.last.text == texts.PUBLISHED_OK

    pubs = await st.rows("SELECT draft_id, matched_by, final_text FROM publications")
    assert [(p[0], p[1], p[2]) for p in pubs] == [(d1, "user_link", EDITED)]
    assert await st.status(d1) == "published"
    kinds = [r[0] for r in await st.rows("SELECT kind FROM jobs ORDER BY created_at, id")]
    assert kinds[:2] == ["writer.generate", "quality.gate"]


async def test_every_decision_emits_event(st: CardsStand) -> None:
    finals = []
    for topic in ("комиссии в l2", "мосты стали скучными", "газ по выходным"):
        finals.append((await st.finals(await st.generate(topic=topic)))[0])
    a, e, r = finals
    await st.press(a, APPROVE)
    await st.press(e, EDIT)
    await st.owner.say(EDITED)
    await st.press(r, REJECT_REASON, "sounds_ai")

    types = [
        t[0]
        for t in await st.rows(
            "SELECT type FROM events WHERE account_id = :a AND type LIKE 'review.%'"
            " ORDER BY ts, id",
            a=st.account_id,
        )
    ]
    assert types == ["review.approved", "review.edited", "review.rejected"]
    assert await st.count("events", "type = 'card.shown' AND account_id = :a",
                          a=st.account_id) == 3  # fmt: skip
    day: date = st.clock.now().date()
    async with st.gs.ws.sf() as session:
        [row] = await compute(session, "zero_edit_rate", st.account_id, day, day)
        [appr] = await compute(session, "approval_rate", st.account_id, day, day)
    assert row.sample_size == 3 and float(row.value or 0) == pytest.approx(1 / 3)
    assert float(appr.value or 0) == pytest.approx(2 / 3)


async def test_bot_never_writes_to_x(st: CardsStand) -> None:
    run = await st.generate()
    d1, d2, _ = await st.finals(run)
    await st.press(d1, APPROVE)
    await st.press(d1, PUBLISHED)
    st.own_post("1900000000000000010", DISTINCT[0])
    await st.owner.say("https://x.com/acc/status/1900000000000000010")
    await st.press(d2, APPROVE)
    st.advance_hours(2)
    st.own_post("1900000000000000011", DISTINCT[1])
    await st.queue.enqueue(PUBLICATIONS_MATCH_KIND, {}, account_id=st.account_id)
    await st.drain()
    assert await st.count("publications") == 2
    assert st.x.calls, "сверка должна читать ленту"
    assert {c.split(":")[0] for c in st.x.calls} <= READ_CALLS
    # подделка X не умеет писать — и бот не пытался: у клиента нет методов записи
    assert not [m for m in dir(st.x) if m.startswith(("post_", "create_", "delete_", "send_"))]
