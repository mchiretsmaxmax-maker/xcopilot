"""Этап 6: обмен кода, хранение токенов в secrets, обновление с блокировкой строки."""

import asyncio
import base64
import json
from collections.abc import Callable
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
import respx

from tests.fakes import FakeClock, FakeNotifier
from xcopilot.db.repos import AccountsRepo, EventsRepo, SecretsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.security.crypto import SecretBox
from xcopilot.x.auth import SECRET_CLIENT, SECRET_TOKENS, TOKEN_URL, XTokens, exchange_code
from xcopilot.x.errors import XAuthError

REDIRECT = "https://example.org/x/callback"
CLIENT_ID = "client-123"
CLIENT_SECRET = "client-secret-xyz"


def _token_body(access: str, refresh: str, expires_in: int = 7200) -> dict[str, object]:
    return {
        "token_type": "bearer",
        "expires_in": expires_in,
        "access_token": access,
        "scope": "tweet.read users.read offline.access",
        "refresh_token": refresh,
    }


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


async def _connect(
    http: httpx.AsyncClient,
    uow_factory: Callable[[], UnitOfWork],
    box: SecretBox,
    clock: FakeClock,
    account_id: UUID,
) -> None:
    await exchange_code(
        http,
        uow_factory,
        box,
        clock,
        account_id=account_id,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        code="auth-code",
        verifier="v" * 43,
        redirect_uri=REDIRECT,
    )


async def test_exchange_code_stores_encrypted_tokens(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, box: SecretBox, xclock: FakeClock
) -> None:
    with respx.mock() as mock:
        route = mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json=_token_body("AT-one", "RT-one"))
        )
        async with httpx.AsyncClient() as http:
            await _connect(http, uow_factory, box, xclock, account_id)
            tokens = XTokens(http, uow_factory, box, xclock, FakeNotifier())
            assert await tokens.get_access_token(account_id) == "AT-one"
    assert route.call_count == 1  # действующий токен выдан без запроса
    req = route.calls.last.request
    assert _form(req) == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": REDIRECT,
        "code_verifier": "v" * 43,
        "client_id": CLIENT_ID,
    }
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert req.headers["authorization"] == f"Basic {basic}"

    async with uow_factory() as uow:
        repo = SecretsRepo(uow.session, account_id)
        tok_row = await repo.get_by_kind(SECRET_TOKENS)
        cli_row = await repo.get_by_kind(SECRET_CLIENT)
        assert tok_row is not None and cli_row is not None
        for raw in (b"AT-one", b"RT-one"):
            assert raw not in tok_row.ciphertext
        assert CLIENT_SECRET.encode() not in cli_row.ciphertext
        stored = json.loads(box.decrypt(tok_row.ciphertext))
        assert stored["access_token"] == "AT-one"
        assert stored["refresh_token"] == "RT-one"
        client = json.loads(box.decrypt(cli_row.ciphertext))
        assert client == {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}


async def test_exchange_code_failure_hides_secrets(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, box: SecretBox, xclock: FakeClock
) -> None:
    with respx.mock() as mock:
        mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_request"})
        )
        async with httpx.AsyncClient() as http:
            with pytest.raises(XAuthError) as exc:
                await _connect(http, uow_factory, box, xclock, account_id)
    for secret in (CLIENT_SECRET, "auth-code", "v" * 43):
        assert secret not in str(exc.value)
    async with uow_factory() as uow:
        assert await SecretsRepo(uow.session, account_id).get_by_kind(SECRET_TOKENS) is None


async def test_refresh_rotates_token_atomically(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, box: SecretBox, xclock: FakeClock
) -> None:
    with respx.mock() as mock:
        route = mock.post(TOKEN_URL).mock(
            side_effect=[
                httpx.Response(200, json=_token_body("AT-one", "RT-one")),
                httpx.Response(200, json=_token_body("AT-two", "RT-two")),
                httpx.Response(200, json=_token_body("AT-three", "RT-three")),
            ]
        )
        async with httpx.AsyncClient() as http:
            await _connect(http, uow_factory, box, xclock, account_id)
            tokens = XTokens(http, uow_factory, box, xclock, FakeNotifier())
            xclock.advance(7200)
            assert await tokens.get_access_token(account_id) == "AT-two"
            assert await tokens.get_access_token(account_id) == "AT-two"
            xclock.advance(7200)
            assert await tokens.get_access_token(account_id) == "AT-three"
    assert route.call_count == 3
    forms = [_form(c.request) for c in route.calls]
    assert forms[1] == {
        "grant_type": "refresh_token",
        "refresh_token": "RT-one",
        "client_id": CLIENT_ID,
    }
    assert forms[2]["refresh_token"] == "RT-two"  # одноразовый refresh заменён новым
    async with uow_factory() as uow:
        plain = await SecretsRepo(uow.session, account_id).get_plain(SECRET_TOKENS, box)
    assert plain is not None
    assert json.loads(plain)["refresh_token"] == "RT-three"


async def test_concurrent_refresh_makes_single_request(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, box: SecretBox, xclock: FakeClock
) -> None:
    with respx.mock() as mock:
        route = mock.post(TOKEN_URL).mock(
            side_effect=[
                httpx.Response(200, json=_token_body("AT-one", "RT-one")),
                httpx.Response(200, json=_token_body("AT-two", "RT-two")),
                httpx.Response(400, json={"error": "invalid_request"}),
            ]
        )
        async with httpx.AsyncClient() as http:
            await _connect(http, uow_factory, box, xclock, account_id)
            xclock.advance(7200)
            # два «процесса» (у каждого свой XTokens) и по два вызова в каждом
            a = XTokens(http, uow_factory, box, xclock, FakeNotifier())
            b = XTokens(http, uow_factory, box, xclock, FakeNotifier())
            results = await asyncio.gather(
                a.get_access_token(account_id),
                b.get_access_token(account_id),
                a.get_access_token(account_id),
                b.get_access_token(account_id),
            )
    assert list(results) == ["AT-two"] * 4
    assert route.call_count == 2  # обмен кода + одно обновление


async def test_refresh_failure_notifies_owner_and_pauses_sync(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID, box: SecretBox, xclock: FakeClock
) -> None:
    notifier = FakeNotifier()
    with respx.mock() as mock:
        mock.post(TOKEN_URL).mock(
            side_effect=[
                httpx.Response(200, json=_token_body("AT-one", "RT-one")),
                httpx.Response(
                    400,
                    json={
                        "error": "invalid_request",
                        "error_description": "Value passed for the token was invalid.",
                    },
                ),
            ]
        )
        async with httpx.AsyncClient() as http:
            await _connect(http, uow_factory, box, xclock, account_id)
            xclock.advance(7200)
            tokens = XTokens(http, uow_factory, box, xclock, notifier)
            with pytest.raises(XAuthError) as exc:
                await tokens.get_access_token(account_id)
    for secret in ("RT-one", "AT-one", CLIENT_SECRET):
        assert secret not in str(exc.value)
    assert len(notifier.sent) == 1
    assert notifier.sent[0][0] == account_id
    assert "Доступ к X" in notifier.sent[0][1]
    async with uow_factory() as uow:
        acc = await AccountsRepo(uow.session).get(account_id)
        assert acc is not None
        assert acc.settings.get("x_sync_paused") is True
        events = await EventsRepo(uow.session, account_id).list()
    failed = [e for e in events if e.type == "x.failed"]
    assert len(failed) == 1
    assert failed[0].props["http_status"] == 400
    assert "RT-one" not in json.dumps(failed[0].props)
