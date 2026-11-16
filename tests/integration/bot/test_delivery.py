"""Аудит D (AUD-004): карточка, не дошедшая до Telegram, не теряется."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import make_cards_stand
from tests.fakes import FakeTelegram
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.review.callbacks import SHOW
from xcopilot.review.settings import ReviewSettings

SF = async_sessionmaker[AsyncSession]


async def no_sleep(_: float) -> None:
    return None


async def test_gate_job_retries_when_card_not_delivered(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    st = await make_cards_stand(session_factory, telegram, deps)
    st.kit.presenter.sleep = no_sleep  # type: ignore[attr-defined]
    telegram.fail_network = 3  # все три попытки отправить карточку падают
    run = await st.generate()
    assert st.cards() == []
    [gate] = await st.rows("SELECT status, attempts FROM jobs WHERE kind = 'quality.gate'")
    assert gate[0] == "queued"  # повтор по правилам очереди, а не «выполнено»
    assert {await st.status(d) for d in await st.finals(run)} == {"judged"}
    judge_calls = len(st.gs.judge.calls)

    st.advance_hours(1)
    await st.drain()
    assert len(st.cards()) == 1
    assert {await st.status(d) for d in await st.finals(run)} == {"shown"}
    assert len(st.gs.judge.calls) == judge_calls  # решение ворот восстановлено, не пересчитано
    [done] = await st.rows("SELECT status FROM jobs WHERE kind = 'quality.gate'")
    assert done[0] == "done"


async def test_show_callback_reports_delivery_failure(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    st = await make_cards_stand(
        session_factory, telegram, deps, settings=ReviewSettings(daily_card_limit=0)
    )
    st.kit.presenter.sleep = no_sleep  # type: ignore[attr-defined]
    run = await st.generate()
    assert st.cards() == []
    first = (await st.finals(run))[0]
    telegram.fail_network = 3
    await st.press(first, SHOW)
    answers = [a for a in telegram.callback_answers() if a.text]
    assert answers, "кнопка должна ответить текстом"
    assert answers[-1].text == texts.CARD_SEND_FAILED
    assert answers[-1].show_alert is True
    assert not [m for m in telegram.sent if "E50" in m.text]
    assert {await st.status(d) for d in await st.finals(run)} == {"judged"}


async def test_show_callback_recovers_after_short_outage(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    st = await make_cards_stand(
        session_factory, telegram, deps, settings=ReviewSettings(daily_card_limit=0)
    )
    st.kit.presenter.sleep = no_sleep  # type: ignore[attr-defined]
    run = await st.generate()
    telegram.fail_network = 1  # первая попытка падает, вторая доходит
    await st.press((await st.finals(run))[0], SHOW)
    assert len(st.cards()) == 1
    assert {await st.status(d) for d in await st.finals(run)} == {"shown"}
