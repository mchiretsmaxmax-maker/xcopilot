"""OAuth 2.0 Authorization Code + PKCE для X: ссылка, обмен кода, хранение и обновление токенов.

Scope только на чтение: `tweet.read users.read offline.access`. Токены и Client Secret
хранятся зашифрованными в `secrets` (kind `x_oauth` и `x_client`) и никогда не попадают
в логи, журнал и тексты ошибок. Refresh-токен одноразовый: обновление идёт под блокировкой
строки секрета (SELECT … FOR UPDATE), чтобы два процесса не потратили его дважды.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import httpx

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.security.crypto import SecretBox

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
SCOPES = ("tweet.read", "users.read", "offline.access")
SECRET_TOKENS = "x_oauth"
SECRET_CLIENT = "x_client"

UowFactory = Callable[[], UnitOfWork]


class OwnerNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


def build_auth_url(client_id: str, redirect_uri: str) -> tuple[str, str, str]:
    """(ссылка авторизации, state, code_verifier)."""
    raise NotImplementedError


def parse_callback(redirect_url: str, expected_state: str) -> str:
    """code из адреса возврата; XStateMismatch при чужом state, XAuthError при отказе."""
    raise NotImplementedError


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
    raise NotImplementedError


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
        self._skew = skew_seconds

    async def get_access_token(self, account_id: UUID) -> str:
        raise NotImplementedError
