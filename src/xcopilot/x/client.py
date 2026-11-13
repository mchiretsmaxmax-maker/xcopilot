"""XClient — интерфейс чтения X для остального кода; XService — реализация по режиму аккаунта.

Режимы: `off` — только oEmbed; `owned` — свои посты через API ($0.001), чужие — oEmbed;
`paid` — чужие посты ($0.005) и профили ($0.010) через API. Каждый вызов API: проверка
бюджета (scope `x_api`) до запроса → запрос → строка в `x_api_calls`.
"""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.x.api_v2 import XApiV2
from xcopilot.x.auth import UowFactory
from xcopilot.x.cost import DailyDedup
from xcopilot.x.models import PostRef, XPost, XUser
from xcopilot.x.oembed import OEmbedReader

XMode = Literal["off", "owned", "paid"]
BUDGET_SCOPE = "x_api"


class BudgetGuardLike(Protocol):
    """Часть BudgetGuard (этап 4), нужная X: проверка до запроса; бросает BudgetExceeded."""

    async def check(self, account_id: UUID, scope: str, estimate: int) -> None: ...


class XClient(Protocol):
    async def get_post(self, ref: PostRef) -> XPost: ...

    async def get_own_posts(self, since_id: str | None = None, limit: int = 100) -> list[XPost]: ...

    async def get_user(self, handle: str) -> XUser: ...


class XService:
    def __init__(
        self,
        *,
        account_id: UUID,
        mode: XMode,
        oembed: OEmbedReader,
        api: XApiV2 | None,
        budget: BudgetGuardLike,
        uow_factory: UowFactory,
        clock: Clock,
        own_handle: str | None = None,
        own_user_id: str | None = None,
        dedup: DailyDedup | None = None,
    ) -> None:
        self.account_id = account_id
        self.mode = mode
        self._oembed = oembed
        self._api = api
        self._budget = budget
        self._uow = uow_factory
        self._clock = clock
        self._own_handle = own_handle
        self._own_user_id = own_user_id
        self._dedup = dedup or DailyDedup()

    async def get_post(self, ref: PostRef) -> XPost:
        raise NotImplementedError

    async def get_own_posts(self, since_id: str | None = None, limit: int = 100) -> list[XPost]:
        raise NotImplementedError

    async def get_user(self, handle: str) -> XUser:
        raise NotImplementedError
