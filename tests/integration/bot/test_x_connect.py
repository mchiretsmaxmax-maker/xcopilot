"""Этап 6: Настройки → «Доступ к X» — режимы, подключение по OAuth PKCE, оценка платного чтения."""

from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.bot.conftest import Client, make_onboarded
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import SecretsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.security.crypto import SecretBox
from xcopilot.x.api_v2 import API_BASE, EP_ME
from xcopilot.x.auth import SECRET_CLIENT, SECRET_TOKENS, TOKEN_URL

SessionFactory = async_sessionmaker[AsyncSession]
CLIENT_ID = "client-id-123456"
CLIENT_SECRET = "client-secret-abcdef"
REDIRECT = "http://127.0.0.1:8765/x/callback"


@pytest.fixture
def key(deps: BotDeps) -> str:
    k = Fernet.generate_key().decode()
    deps.settings = deps.settings.model_copy(update={"secrets_key": k})
    return k


async def _acc(sf: SessionFactory) -> dict[str, object]:
    async with sf() as s:
        row = await s.execute(
            text("SELECT id, x_read_mode, x_user_id FROM accounts WHERE handle = 'acc'")
        )
        return dict(row.one()._mapping)


async def _x_calls(sf: SessionFactory) -> list[tuple[str, int]]:
    async with sf() as s:
        rows = await s.execute(text("SELECT endpoint, cost_micro_usd FROM x_api_calls"))
        return [(r[0], r[1]) for r in rows]


async def _to_auth_link(owner: Client, mode_label: str = "Только мой аккаунт") -> str:
    await owner.say("/settings")
    await owner.press("Доступ к X")
    await owner.press(mode_label)
    if mode_label == "Платное чтение":
        await owner.press("Включить")
    assert "Client ID" in owner.last.text
    sent = await owner.say("не id с пробелами")
    assert "Не похоже на Client ID" in sent[-1].text
    await owner.say(CLIENT_ID)
    assert "Client Secret" in owner.last.text
    sent = await owner.say(CLIENT_SECRET)
    msg = sent[-1].text
    assert "После входа скопируй адрес страницы" in msg
    url = next(w for w in msg.split() if w.startswith("https://x.com/i/oauth2/authorize"))
    return parse_qs(urlsplit(url).query)["state"][0]


def _x_mock() -> respx.MockRouter:
    mock = respx.mock(assert_all_called=False)
    mock.post(TOKEN_URL, name="token").mock(
        return_value=httpx.Response(
            200,
            json={
                "token_type": "bearer",
                "expires_in": 7200,
                "access_token": "AT-one",
                "refresh_token": "RT-one",
                "scope": "tweet.read users.read offline.access",
            },
        )
    )
    mock.get(f"{API_BASE}/2/users/me", name="me").mock(
        return_value=httpx.Response(200, json={"data": {"id": "1500", "username": "acc"}})
    )
    return mock


async def test_x_access_menu_in_settings(owner: Client, session_factory: SessionFactory) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/settings")
    assert "Доступ к X" in sent[-1].buttons
    sent = await owner.press("Доступ к X")
    assert sent[-1].buttons == ["Выключен", "Только мой аккаунт", "Платное чтение"]
    assert "не подключён" in sent[-1].text
    await owner.press("Выключен")
    assert (await _acc(session_factory))["x_read_mode"] == "off"


async def test_owned_connect_flow_stores_tokens_and_user_id(
    owner: Client, session_factory: SessionFactory, key: str
) -> None:
    await make_onboarded(session_factory)
    with _x_mock() as mock:
        state = await _to_auth_link(owner)
        # секрет не лежит открытым текстом в FSM
        data = await owner.data()
        assert CLIENT_SECRET not in str(data)
        sent = await owner.say(f"{REDIRECT}?state={state}&code=the-code")
        token_calls = mock["token"].call_count
        me_calls = mock["me"].call_count
    assert token_calls == 1
    assert me_calls == 1
    assert "X подключён как @acc" in sent[-1].text
    assert await owner.state() is None
    acc = await _acc(session_factory)
    assert acc["x_read_mode"] == "owned"
    assert acc["x_user_id"] == "1500"
    account_id = acc["id"]
    assert isinstance(account_id, UUID)
    async with UnitOfWork(session_factory) as uow:
        repo = SecretsRepo(uow.session, account_id)
        box = SecretBox.from_string(key)
        assert await repo.get_plain(SECRET_TOKENS, box) is not None
        client = await repo.get_plain(SECRET_CLIENT, box)
    assert client is not None and CLIENT_SECRET in client
    assert await _x_calls(session_factory) == [(EP_ME, 1_000)]
    for msg in owner.tg.sent:
        assert CLIENT_SECRET not in msg.text
        assert "AT-one" not in msg.text


async def test_wrong_state_rejected_without_token_request(
    owner: Client, session_factory: SessionFactory, key: str
) -> None:
    await make_onboarded(session_factory)
    with _x_mock() as mock:
        await _to_auth_link(owner)
        sent = await owner.say(f"{REDIRECT}?state=evil&code=the-code")
        assert "не от этой попытки" in sent[-1].text
        sent = await owner.say("просто текст")
        assert "не похоже на адрес" in sent[-1].text.lower()
        token_calls = mock["token"].call_count
    assert token_calls == 0
    assert (await _acc(session_factory))["x_read_mode"] == "owned"


async def test_paid_shows_estimate_before_enabling(
    owner: Client, session_factory: SessionFactory, key: str
) -> None:
    account_id = await make_onboarded(session_factory)
    await owner.say("/settings")
    await owner.press("Доступ к X")
    sent = await owner.press("Платное чтение")
    assert "Чужой пост — $0.005" in sent[-1].text
    assert "При лимите $1 в день это до 200 постов" in sent[-1].text
    assert sent[-1].buttons == ["Включить", "Отмена"]
    await owner.press("Отмена")
    assert (await _acc(session_factory))["x_read_mode"] == "owned"

    # уже подключён — платное чтение включается сразу
    async with UnitOfWork(session_factory) as uow:
        await SecretsRepo(uow.session, account_id).put(
            SECRET_TOKENS, "{}", SecretBox.from_string(key)
        )
    await owner.press("Платное чтение")
    await owner.press("Включить")
    assert (await _acc(session_factory))["x_read_mode"] == "paid"


async def test_paid_connect_flow_sets_paid(
    owner: Client, session_factory: SessionFactory, key: str
) -> None:
    await make_onboarded(session_factory)
    with _x_mock():
        state = await _to_auth_link(owner, "Платное чтение")
        await owner.say(f"{REDIRECT}?code=c&state={state}")
    assert (await _acc(session_factory))["x_read_mode"] == "paid"


async def test_connect_without_secrets_key_is_refused(
    owner: Client, session_factory: SessionFactory
) -> None:
    await make_onboarded(session_factory)
    await owner.say("/settings")
    await owner.press("Доступ к X")
    await owner.press("Только мой аккаунт")
    await owner.say(CLIENT_ID)
    sent = await owner.say(CLIENT_SECRET)
    assert "SECRETS_KEY" in sent[-1].text
    assert await owner.state() is None


async def test_token_exchange_rejected(
    owner: Client, session_factory: SessionFactory, key: str
) -> None:
    await make_onboarded(session_factory)
    with respx.mock() as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "x"}))
        state = await _to_auth_link(owner)
        sent = await owner.say(f"{REDIRECT}?state={state}&code=bad")
    assert "Подключить X не вышло" in sent[-1].text
    assert await owner.state() is None
    assert (await _acc(session_factory))["x_read_mode"] == "owned"
