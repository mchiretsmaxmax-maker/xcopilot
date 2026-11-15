"""Этап 12: подтверждение публикации ссылкой и автосверка со своей лентой."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.writer_stand import DISTINCT
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.review.callbacks import APPROVE, PUBLISHED
from xcopilot.review.jobs import PUBLICATIONS_MATCH_KIND
from xcopilot.review.matcher import similarity
from xcopilot.review.publish import ForeignLinkError

SF = async_sessionmaker[AsyncSession]
POST_ID = "1900000000000000001"
AMBIGUOUS = "блобы снизили комиссии роллапов, это заметно по транзакциям за последний месяц"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


async def approved(st: CardsStand) -> UUID:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    await st.press(d1, APPROVE)
    return d1


async def publications(st: CardsStand) -> list[Any]:
    return await st.rows(
        "SELECT draft_id, x_post_id, url, matched_by, match_score, final_text FROM publications"
        " WHERE account_id = :a",
        a=st.account_id,
    )


async def match_now(st: CardsStand) -> None:
    await st.queue.enqueue(PUBLICATIONS_MATCH_KIND, {}, account_id=st.account_id)
    await st.drain()


async def test_confirm_with_link_creates_publication_with_post_id(st: CardsStand) -> None:
    d1 = await approved(st)
    asked = await st.press(d1, PUBLISHED)
    assert asked[-1].text == texts.ASK_LINK and asked[-1].buttons == ["Найди сам"]
    st.own_post(POST_ID, DISTINCT[0])
    sent = await st.owner.say(f"вот: https://x.com/acc/status/{POST_ID}?s=20")
    assert sent[-1].text == texts.PUBLISHED_OK
    [(draft_id, x_post_id, url, matched_by, score, final)] = await publications(st)
    assert draft_id == d1 and x_post_id == POST_ID and matched_by == "user_link"
    assert url == f"https://x.com/acc/status/{POST_ID}" and score is None
    assert final == DISTINCT[0]
    assert await st.status(d1) == "published"
    assert await st.count("events", "type = 'publication.confirmed'") == 1
    assert await st.owner.state() is None


async def test_confirm_with_foreign_link_rejected(st: CardsStand) -> None:
    d1 = await approved(st)
    await st.press(d1, PUBLISHED)
    st.own_post("77", DISTINCT[0], handle="someone")
    sent = await st.owner.say("https://x.com/someone/status/77")
    assert sent[-1].text == texts.LINK_FOREIGN
    bad = await st.owner.say("это не ссылка")
    assert bad[-1].text == texts.LINK_BAD
    assert await publications(st) == []
    assert await st.status(d1) == "approved"
    with pytest.raises(ForeignLinkError):
        await st.kit.publisher.confirm_published(
            st.account_id, d1, "https://twitter.com/Someone/status/77"
        )
    # свой хэндл в другом регистре — свой
    st.own_post(POST_ID, DISTINCT[0])
    await st.owner.say(f"https://twitter.com/ACC/status/{POST_ID}")
    assert len(await publications(st)) == 1


async def test_auto_match_above_085(st: CardsStand) -> None:
    d1 = await approved(st)
    st.advance_hours(2)
    st.own_post(POST_ID, DISTINCT[0] + " 🙂")
    st.own_post("1900000000000000002", "совсем другой пост про погоду и кофе")
    await match_now(st)
    [(draft_id, x_post_id, _, matched_by, score, _)] = await publications(st)
    assert draft_id == d1 and x_post_id == POST_ID and matched_by == "auto"
    assert score >= 0.85
    assert await st.status(d1) == "published"
    assert await st.count("events", "type = 'publication.matched'") == 1
    await match_now(st)  # повтор не создаёт второй публикации
    assert len(await publications(st)) == 1


async def test_ambiguous_match_asks_owner(st: CardsStand) -> None:
    assert 0.6 <= similarity(DISTINCT[0], AMBIGUOUS) < 0.85
    d1 = await approved(st)
    st.advance_hours(1)
    st.own_post(POST_ID, AMBIGUOUS)
    await match_now(st)
    assert await publications(st) == []
    ask = st.owner.last
    assert texts.MATCH_ASK in ask.text and AMBIGUOUS in ask.text
    assert ask.buttons == ["Да, это он", "Нет"]
    await st.owner.press("Да, это он")
    [(draft_id, x_post_id, _, matched_by, _score, final)] = await publications(st)
    assert draft_id == d1 and x_post_id == POST_ID and matched_by == "manual"
    assert final == AMBIGUOUS
    assert await st.status(d1) == "published"


async def test_no_match_after_48h_marks_unconfirmed(st: CardsStand) -> None:
    d1 = await approved(st)
    st.own_post(POST_ID, "совсем другой пост про погоду и кофе")
    st.advance_hours(10)
    await match_now(st)
    assert await st.status(d1) == "approved"  # окно ещё открыто
    st.advance_hours(40)
    await match_now(st)
    assert await st.status(d1) == "unconfirmed"
    assert await publications(st) == []


async def test_final_text_taken_from_published_post(st: CardsStand) -> None:
    d1 = await approved(st)
    published = DISTINCT[0].replace("за неделю", "за эту неделю")
    st.own_post(POST_ID, published)
    await st.press(d1, PUBLISHED)
    await st.owner.say(f"https://x.com/acc/status/{POST_ID}")
    [(_, _, _, _, _, final)] = await publications(st)
    assert final == published != DISTINCT[0]
