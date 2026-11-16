"""Задача B аудита (AUD-005): имя аккаунта X обновляется при подключении и в настройках."""

from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import Client, make_onboarded
from tests.integration.bot.test_x_connect import REDIRECT, _to_auth_link, _x_mock
from tests.writer_stand import DISTINCT
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.x.api_v2 import API_BASE

SF = async_sessionmaker[AsyncSession]


@pytest.fixture
def key(deps: BotDeps) -> str:
    k = Fernet.generate_key().decode()
    deps.settings = deps.settings.model_copy(update={"secrets_key": k})
    return k


def _me(mock: respx.MockRouter, username: str) -> None:
    mock.get(f"{API_BASE}/2/users/me", name="me").mock(
        return_value=httpx.Response(200, json={"data": {"id": "1500", "username": username}})
    )


async def _handle(sf: SF, handle: str) -> str | None:
    async with sf() as s:
        row = await s.execute(
            text("SELECT handle FROM accounts WHERE kind = 'x' AND handle = :h"), {"h": handle}
        )
        return row.scalar_one_or_none()


async def _self_authors(sf: SF) -> list[str]:
    async with sf() as s:
        rows = await s.execute(text("SELECT handle FROM authors WHERE is_self"))
        return [r[0] for r in rows]


async def _connect(owner: Client) -> None:
    state = await _to_auth_link(owner)
    await owner.say(f"{REDIRECT}?state={state}&code=the-code")


async def test_x_connect_updates_account_handle(
    owner: Client, session_factory: SF, key: str
) -> None:
    await make_onboarded(session_factory, handle="user123")
    with _x_mock() as mock:
        _me(mock, "RealName")
        await _connect(owner)
    assert await _handle(session_factory, "realname") == "realname"
    assert await _handle(session_factory, "user123") is None
    assert await _self_authors(session_factory) == ["realname"]


async def test_x_connect_keeps_handle_when_taken(
    owner: Client, session_factory: SF, key: str
) -> None:
    await make_onboarded(session_factory, handle="user123")
    async with UnitOfWork(session_factory) as uow:
        await AccountsRepo(uow.session).create(handle="realname")
    with capture_logs() as logs, _x_mock() as mock:
        _me(mock, "RealName")
        await _connect(owner)
    assert await _handle(session_factory, "user123") == "user123"
    assert await owner.state() is None
    assert any(e["event"] == "account_handle_conflict" for e in logs)


async def test_settings_set_handle(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory, handle="user123")
    sent = await owner.say("/settings")
    assert "Мой аккаунт X" in sent[-1].buttons
    assert "user123" in sent[-1].text
    await owner.press("Мой аккаунт X")
    sent = await owner.say("не хэндл с пробелами")
    assert await owner.state() is not None
    assert await _handle(session_factory, "user123") == "user123"
    sent = await owner.say("@NewName")
    assert "newname" in sent[-1].text
    assert await _handle(session_factory, "newname") == "newname"
    assert await owner.state() is None
    assert await _self_authors(session_factory) == ["newname"]
    await owner.press_data("set:handle")
    await owner.say("/cancel")
    assert await owner.state() is None


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


async def test_publish_link_accepted_after_handle_update(
    st: CardsStand, session_factory: SF
) -> None:
    from xcopilot.review.callbacks import APPROVE

    run = await st.generate()
    d1 = (await st.finals(run))[0]
    await st.press(d1, APPROVE)
    async with UnitOfWork(session_factory) as uow:
        assert await AccountsRepo(uow.session).set_handle(st.account_id, "realname")
    st.own_post("1", DISTINCT[0], handle="realname")
    confirmed = await st.kit.publisher.confirm_published(
        st.account_id, d1, "https://x.com/RealName/status/1"
    )
    assert confirmed is not None
