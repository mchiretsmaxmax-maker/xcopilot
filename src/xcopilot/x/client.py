"""XClient — интерфейс чтения X для остального кода; XService — реализация по режиму аккаунта.

Режимы: `off` — только oEmbed; `owned` — свои посты через API ($0.001), чужие — oEmbed;
`paid` — чужие посты ($0.005) и профили ($0.010) через API. Каждый вызов API: проверка
бюджета (scope `x_api`) до запроса → запрос → строка в `x_api_calls`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date
from typing import Literal, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.errors import BudgetExceeded
from xcopilot.metrics.events import EventType, emit
from xcopilot.x import cost
from xcopilot.x.api_v2 import XApiV2
from xcopilot.x.auth import UowFactory
from xcopilot.x.cost import DailyDedup
from xcopilot.x.errors import XModeError, XUnavailable
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


class CostMeter:
    """CallMeter аккаунта: бюджет до запроса, строка x_api_calls после (по виду ресурса)."""

    def __init__(
        self,
        *,
        account_id: UUID,
        kind: str,
        budget: BudgetGuardLike,
        uow_factory: UowFactory,
        clock: Clock,
        dedup: DailyDedup,
    ) -> None:
        self._account_id = account_id
        self._kind = kind
        self._budget = budget
        self._uow = uow_factory
        self._clock = clock
        self._dedup = dedup

    def _day(self) -> date:
        return self._clock.now().astimezone(UTC).date()

    async def before(
        self, endpoint: str, resource_ids: Sequence[str] | None, max_resources: int
    ) -> None:
        if resource_ids is None:
            n = max_resources
        else:
            n = len(self._dedup.new_ids(self._account_id, self._day(), self._kind, resource_ids))
        estimate = cost.estimate(self._kind, n)
        await self._budget.check(self._account_id, BUDGET_SCOPE, estimate)

    async def after(
        self, endpoint: str, resource_ids: Sequence[str], http_status: int | None, ok: bool
    ) -> None:
        day = self._day()
        charged = self._dedup.mark(self._account_id, day, self._kind, resource_ids) if ok else []
        async with self._uow() as uow:
            await cost.record(
                uow.session,
                self._account_id,
                endpoint=endpoint,
                resources=len(charged),
                cost_micro_usd=cost.estimate(self._kind, len(charged)),
                utc_day=day,
                http_status=http_status,
                ok=ok,
            )
            if not ok:
                await emit(
                    uow.session,
                    account_id=self._account_id,
                    type=EventType.X_FAILED,
                    props={"endpoint": endpoint, "http_status": http_status, "error": "http"},
                )


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

    def _meter(self, kind: str) -> CostMeter:
        return CostMeter(
            account_id=self.account_id,
            kind=kind,
            budget=self._budget,
            uow_factory=self._uow,
            clock=self._clock,
            dedup=self._dedup,
        )

    def _require_api(self, what: str) -> XApiV2:
        if self._api is None:
            raise XModeError(f"{what}: X API не подключён")
        return self._api

    def _is_own(self, ref: PostRef) -> bool:
        own = self._own_handle
        return bool(own and ref.handle and ref.handle.lower() == own.lower())

    async def get_post(self, ref: PostRef) -> XPost:
        """Пост по ссылке. off — oEmbed; owned — свой через API, чужой — oEmbed;
        paid — через API. Сбой API или исчерпанный бюджет → бесплатный oEmbed."""
        if self.mode == "off" or self._api is None:
            return await self._oembed.fetch(ref)
        if self.mode == "owned" and not self._is_own(ref):
            return await self._oembed.fetch(ref)
        kind = cost.OWNED_POST if self._is_own(ref) else cost.POST
        try:
            return await self._api.get_post(ref.id, meter=self._meter(kind))
        except (BudgetExceeded, XUnavailable):
            return await self._oembed.fetch(ref)

    async def get_own_posts(self, since_id: str | None = None, limit: int = 100) -> list[XPost]:
        """Свои посты с метриками (новые первыми). Недоступно в режиме off."""
        if self.mode == "off":
            raise XModeError("Режим «Выключен»: свои посты из X не читаются")
        api = self._require_api("свои посты")
        if not self._own_user_id:
            raise XModeError("Не известен id своего аккаунта в X: подключи доступ к X")
        posts = await api.get_own_posts(
            self._own_user_id, since_id, limit, meter=self._meter(cost.OWNED_POST)
        )
        return [replace(p, author_handle=p.author_handle or self._own_handle) for p in posts]

    async def get_user(self, handle: str) -> XUser:
        """Профиль по хэндлу — только в режиме paid ($0.010)."""
        if self.mode != "paid":
            raise XModeError("Профили читаются только в режиме «Платное чтение»")
        api = self._require_api("профиль")
        return await api.get_user(handle, meter=self._meter(cost.USER))
