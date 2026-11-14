"""XClient — интерфейс чтения X для остального кода; XService — реализация по режиму аккаунта.

Режимы: `off` — только oEmbed; `owned` — свои посты через API ($0.001), чужие — oEmbed;
`lookup` (v3.1) — по API только посты и профили из присланных владельцем ссылок, бюджет
scope `x_lookup`, фонового опроса лент нет; `paid` — чужие посты ($0.005) и профили
($0.010) через API. Каждый вызов API: проверка бюджета до запроса → запрос → строка в
`x_api_calls`. Пост моложе 24 часов ставится на перепроверку метрик (`x.recheck_metrics`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, date, timedelta
from typing import Literal, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.repos import AuthorsRepo
from xcopilot.errors import BudgetExceeded
from xcopilot.jobs.queue import JobQueue
from xcopilot.metrics.events import EventType, emit
from xcopilot.x import cost
from xcopilot.x.api_v2 import XApiV2
from xcopilot.x.auth import UowFactory
from xcopilot.x.cost import DailyDedup
from xcopilot.x.errors import XModeError, XUnavailable
from xcopilot.x.models import PostRef, XPost, XUser
from xcopilot.x.oembed import OEmbedReader

XMode = Literal["off", "owned", "lookup", "paid"]
BUDGET_SCOPE = "x_api"
LOOKUP_SCOPE = "x_lookup"
RECHECK_KIND = "x.recheck_metrics"
RECHECK_AFTER = timedelta(hours=24)
FOLLOWERS_TTL = timedelta(days=30)


class BudgetGuardLike(Protocol):
    """Часть BudgetGuard (этап 4), нужная X: проверка до запроса; бросает BudgetExceeded."""

    async def check(self, account_id: UUID, scope: str, estimate: int) -> None: ...


class XClient(Protocol):
    async def get_post(self, ref: PostRef) -> XPost: ...

    async def get_own_posts(self, since_id: str | None = None, limit: int = 100) -> list[XPost]: ...

    async def get_user(self, handle: str) -> XUser: ...

    def iter_own_history(self, limit: int = 500) -> AsyncIterator[XPost]: ...


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
        scope: str = BUDGET_SCOPE,
    ) -> None:
        self._scope = scope
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
        await self._budget.check(self._account_id, self._scope, estimate)

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
        jobs: JobQueue | None = None,
    ) -> None:
        self._jobs = jobs
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

    def _meter(self, kind: str, scope: str = BUDGET_SCOPE) -> CostMeter:
        return CostMeter(
            scope=scope,
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
        lookup — через API на отдельный бюджет; paid — через API. Сбой API или исчерпанный
        бюджет → бесплатный oEmbed."""
        if self.mode == "off" or self._api is None:
            return await self._oembed.fetch(ref)
        if self.mode == "lookup":
            return await self.lookup_post(ref)
        if self.mode == "owned" and not self._is_own(ref):
            return await self._oembed.fetch(ref)
        return await self._read_api(ref, BUDGET_SCOPE)

    async def lookup_post(self, ref: PostRef) -> XPost:
        """Точечное чтение присланного поста по API (режим lookup, scope x_lookup)."""
        if self.mode != "lookup" or self._api is None:
            return await self.get_post(ref)
        return await self._read_api(ref, LOOKUP_SCOPE)

    async def _read_api(self, ref: PostRef, scope: str) -> XPost:
        assert self._api is not None
        kind = cost.OWNED_POST if self._is_own(ref) else cost.POST
        try:
            post = await self._api.get_post(ref.id, meter=self._meter(kind, scope))
        except (BudgetExceeded, XUnavailable):
            return await self._oembed.fetch(ref)
        await self._schedule_recheck(post)
        return post

    async def _schedule_recheck(self, post: XPost) -> None:
        """Пост моложе 24 часов: метрики ещё растут — перечитать через сутки после публикации."""
        created = post.created_at
        if self._jobs is None or post.source != "api" or created is None:
            return
        due = created + RECHECK_AFTER
        if due <= self._clock.now():
            return
        await self._jobs.enqueue(
            RECHECK_KIND,
            {"post_id": post.id, "handle": post.author_handle},
            account_id=self.account_id,
            run_at=due,
            idempotency_key=f"{RECHECK_KIND}:{post.id}",
        )

    async def get_own_posts(self, since_id: str | None = None, limit: int = 100) -> list[XPost]:
        """Свои посты с метриками (новые первыми). Недоступно в режимах off и lookup."""
        if self.mode == "off":
            raise XModeError("Режим «Выключен»: свои посты из X не читаются")
        if self.mode == "lookup":
            raise XModeError("Режим «Точечное чтение»: ленты не опрашиваются")
        api = self._require_api("свои посты")
        if not self._own_user_id:
            raise XModeError("Не известен id своего аккаунта в X: подключи доступ к X")
        posts = await api.get_own_posts(
            self._own_user_id, since_id, limit, meter=self._meter(cost.OWNED_POST)
        )
        return [replace(p, author_handle=p.author_handle or self._own_handle) for p in posts]

    async def iter_own_history(self, limit: int = 500) -> AsyncIterator[XPost]:
        """Своя история постранично до limit постов (импорт, этап 14). owned и paid."""
        if self.mode not in ("owned", "paid"):
            raise XModeError("Своя история читается в режимах «Только мой аккаунт» и «Платное»")
        api = self._require_api("своя история")
        if not self._own_user_id:
            raise XModeError("Не известен id своего аккаунта в X: подключи доступ к X")
        async for p in api.iter_own_history(
            self._own_user_id, limit, meter=self._meter(cost.OWNED_POST)
        ):
            yield replace(p, author_handle=p.author_handle or self._own_handle)

    async def get_user(self, handle: str) -> XUser:
        """Профиль с числом подписчиков: lookup и paid ($0.010). Подписчики кэшируются в
        `authors` на 30 дней — повторный вопрос в срок не идёт в API."""
        if self.mode not in ("lookup", "paid"):
            raise XModeError("Профили читаются в режимах «Точечное чтение» и «Платное чтение»")
        cached = await self._cached_user(handle)
        if cached is not None:
            return cached
        api = self._require_api("профиль")
        scope = LOOKUP_SCOPE if self.mode == "lookup" else BUDGET_SCOPE
        user = await api.get_user(handle, meter=self._meter(cost.USER, scope))
        await self._store_user(user)
        return user

    async def _cached_user(self, handle: str) -> XUser | None:
        try:
            async with self._uow() as uow:
                author = await AuthorsRepo(uow.session, self.account_id).by_handle(handle)
        except ValueError:
            return None
        if (
            author is None
            or author.followers is None
            or author.followers_observed_at is None
            or author.x_user_id is None
            or self._clock.now() - author.followers_observed_at >= FOLLOWERS_TTL
        ):
            return None
        return XUser(
            id=author.x_user_id,
            handle=author.handle,
            name=author.display_name,
            followers=author.followers,
        )

    async def _store_user(self, user: XUser) -> None:
        if user.followers is None:
            return
        async with self._uow() as uow:
            repo = AuthorsRepo(uow.session, self.account_id)
            author = await repo.get_or_create(user.handle)
            await repo.update(
                author.id,
                x_user_id=user.id,
                display_name=user.name or author.display_name,
                followers=user.followers,
                followers_observed_at=self._clock.now(),
                followers_origin="x_api",
            )
