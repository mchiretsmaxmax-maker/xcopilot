"""OAuth 2.0 Authorization Code + PKCE для X: ссылка, обмен кода, хранение и обновление токенов.

Scope только на чтение: `tweet.read users.read offline.access`. Токены и Client Secret
хранятся зашифрованными в `secrets` (kind `x_oauth` и `x_client`) и никогда не попадают
в логи, журнал и тексты ошибок. Refresh-токен одноразовый: обновление идёт под блокировкой
строки секрета (SELECT … FOR UPDATE), чтобы два процесса не потратили его дважды.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, SecretsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.metrics.events import EventType, emit
from xcopilot.security.crypto import SecretBox
from xcopilot.x.errors import XAuthError, XStateMismatch, XUnavailable

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
SCOPES = ("tweet.read", "users.read", "offline.access")
SECRET_TOKENS = "x_oauth"
SECRET_CLIENT = "x_client"
TOKEN_ENDPOINT = "POST /2/oauth2/token"
PAUSED_SETTING = "x_sync_paused"
RECONNECT_TEXT = (
    "Доступ к X потерян: X не принял токен. Синхронизация на паузе. "
    "Подключи заново: Настройки → «Доступ к X»."
)

UowFactory = Callable[[], UnitOfWork]


class OwnerNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scope: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at.isoformat(),
                "scope": self.scope,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> _Tokens:
        d = json.loads(raw)
        return cls(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            expires_at=datetime.fromisoformat(d["expires_at"]),
            scope=d.get("scope", ""),
        )

    @classmethod
    def from_response(cls, body: dict[str, Any], now: datetime) -> _Tokens:
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise XAuthError("X: в ответе нет access_token")
        expires_in = body.get("expires_in")
        seconds = expires_in if isinstance(expires_in, int) else 7200
        refresh = body.get("refresh_token")
        return cls(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else None,
            expires_at=now + timedelta(seconds=seconds),
            scope=str(body.get("scope", "")),
        )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def build_auth_url(client_id: str, redirect_uri: str) -> tuple[str, str, str]:
    """(ссылка авторизации, state, code_verifier)."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 символов из [A-Za-z0-9_-]
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}", state, verifier


def parse_callback(redirect_url: str, expected_state: str) -> str:
    """code из адреса возврата; XStateMismatch при чужом state, XAuthError при отказе."""
    parts = urlsplit(redirect_url.strip())
    if not parts.scheme or not parts.netloc:
        raise XAuthError("Это не похоже на адрес страницы: пришли адрес целиком")
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    if "error" in q:
        raise XAuthError("X: вход отклонён или отменён")
    state = q.get("state", "")
    if not secrets.compare_digest(state.encode(), expected_state.encode()):
        raise XStateMismatch("Адрес не от этой попытки входа: начни подключение заново")
    code = q.get("code")
    if not code:
        raise XAuthError("В адресе нет кода авторизации")
    return code


async def _token_request(
    http: httpx.AsyncClient, form: dict[str, str], client_id: str, client_secret: str | None
) -> httpx.Response:
    auth = httpx.BasicAuth(client_id, client_secret) if client_secret else None
    try:
        return await http.request(
            "POST",
            TOKEN_URL,
            data=form,
            auth=auth if auth is not None else httpx.USE_CLIENT_DEFAULT,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise XUnavailable(f"X: сервер токенов недоступен ({type(exc).__name__})") from None


def _body(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise XUnavailable(f"X: неожиданный ответ сервера токенов ({resp.status_code})")
    return data


async def exchange_code(
    http: httpx.AsyncClient,
    uow_factory: UowFactory,
    box: SecretBox,
    clock: Clock,
    *,
    account_id: UUID,
    client_id: str,
    client_secret: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> None:
    """Обменять код на токены и сохранить их и учётку клиента зашифрованными."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "client_id": client_id,
    }
    resp = await _token_request(http, form, client_id, client_secret)
    if resp.status_code >= 500:
        raise XUnavailable(f"X: сервер токенов вернул {resp.status_code}")
    if resp.status_code != 200:
        raise XAuthError(f"X не выдал токен (HTTP {resp.status_code}): начни подключение заново")
    tokens = _Tokens.from_response(_body(resp), clock.now())
    client = json.dumps({"client_id": client_id, "client_secret": client_secret})
    async with uow_factory() as uow:
        repo = SecretsRepo(uow.session, account_id)
        await repo.put(SECRET_CLIENT, client, box)
        await repo.put(SECRET_TOKENS, tokens.to_json(), box)
        await AccountsRepo(uow.session).update_settings(account_id, **{PAUSED_SETTING: False})


class XTokens:
    """Выдаёт действующий access-токен аккаунта, при истечении обновляет его."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        uow_factory: UowFactory,
        box: SecretBox,
        clock: Clock,
        notifier: OwnerNotifier,
        *,
        skew_seconds: int = 60,
    ) -> None:
        self._http = http
        self._uow = uow_factory
        self._box = box
        self._clock = clock
        self._notifier = notifier
        self._skew = timedelta(seconds=skew_seconds)
        self._locks: dict[UUID, asyncio.Lock] = {}

    def _fresh(self, tokens: _Tokens) -> bool:
        return tokens.expires_at - self._skew > self._clock.now()

    async def get_access_token(self, account_id: UUID) -> str:
        async with self._uow() as uow:
            raw = await SecretsRepo(uow.session, account_id).get_plain(SECRET_TOKENS, self._box)
        if raw is None:
            raise XAuthError("X не подключён: Настройки → «Доступ к X»")
        tokens = _Tokens.from_json(raw)
        if self._fresh(tokens):
            return tokens.access_token
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            return await self._refresh(account_id)

    async def _refresh(self, account_id: UUID) -> str:
        failed_status: int | None = None
        async with self._uow() as uow:
            repo = SecretsRepo(uow.session, account_id)
            row = await repo.get_by_kind_for_update(SECRET_TOKENS)
            client_raw = await repo.get_plain(SECRET_CLIENT, self._box)
            if row is None or client_raw is None:
                raise XAuthError("X не подключён: Настройки → «Доступ к X»")
            tokens = _Tokens.from_json(self._box.decrypt(row.ciphertext))
            if self._fresh(tokens):  # другой процесс уже обновил, пока мы ждали блокировку
                return tokens.access_token
            if not tokens.refresh_token:
                failed_status = 0
            else:
                client = json.loads(client_raw)
                form = {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "client_id": client["client_id"],
                }
                resp = await _token_request(
                    self._http, form, client["client_id"], client.get("client_secret")
                )
                if resp.status_code >= 500:
                    raise XUnavailable(f"X: сервер токенов вернул {resp.status_code}")
                if resp.status_code == 200:
                    new = _Tokens.from_response(_body(resp), self._clock.now())
                    if new.refresh_token is None:
                        new = _Tokens(
                            new.access_token, tokens.refresh_token, new.expires_at, new.scope
                        )
                    await repo.put(SECRET_TOKENS, new.to_json(), self._box)
                    return new.access_token
                failed_status = resp.status_code
        await self._pause(account_id, failed_status)
        raise XAuthError("X не принял токен: подключи доступ к X заново")

    async def _pause(self, account_id: UUID, http_status: int | None) -> None:
        async with self._uow() as uow:
            await AccountsRepo(uow.session).update_settings(account_id, **{PAUSED_SETTING: True})
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.X_FAILED,
                props={
                    "endpoint": TOKEN_ENDPOINT,
                    "http_status": http_status or None,
                    "error": "refresh_failed",
                },
            )
        await self._notifier.notify_owner(account_id, RECONNECT_TEXT)
