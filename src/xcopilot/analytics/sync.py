"""Синк своей ленты и снимки метрик поста (ТЗ, этап 14, задачи 1–4, 12–14).

Раз в день `sync_own_posts` читает новые свои посты (`since_id`, owned reads) и для каждого:
- кладёт материал `stance = own`, `origin = x_api` с текстом и сигналом `own_sync`;
- ставит `ingest.process` (куски, теги, векторы — своя лента попадает в паспорт, ретривал и
  метрику дрейфа голоса) и `brain.analyze` (разбор, этапы 7A/9A);
- снимает первый снимок метрик и ставит контрольные снимки 1ч/24ч/72ч/7д (`analytics.snapshot`).
После синка запускается автосверка публикаций, затем у постов проставляется «с ботом / без
бота» (`sources.meta.bot_assisted`) по таблице `publications`.

На отметке 24 часа `take_snapshot` считает исход поста (перцентиль среди своих постов формата),
находит в тексте знания базы и пишет доказательства `own_outcome` (v3.1) — и для постов бота,
и для постов без бота.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import structlog

from xcopilot.brain import outcomes, signals
from xcopilot.brain.analyze import ANALYZE_KIND
from xcopilot.brain.evidence import record_own_outcome
from xcopilot.brain.scoring import recompute
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, AnalyticsRepo, SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.ingest.service import INGEST_KIND
from xcopilot.jobs.queue import JobQueue
from xcopilot.metrics.events import EventType, emit
from xcopilot.x.auth import PAUSED_SETTING, RECONNECT_TEXT
from xcopilot.x.client import XClient
from xcopilot.x.errors import XAuthError, XModeError, XNotFound
from xcopilot.x.models import PostRef, XPost

if TYPE_CHECKING:
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.review.matcher import Match, PublicationMatcher

log = structlog.get_logger(__name__)

SYNC_KIND = "analytics.sync"
SNAPSHOT_KIND = "analytics.snapshot"
CHECKPOINT_MINUTES = (60, 24 * 60, 72 * 60, 7 * 24 * 60)
OUTCOME_CHECKPOINT = 24 * 60
OWN_POSTS_LIMIT = 100
NO_READ_MODES = frozenset({"off", "lookup"})  # ленту читают только owned и paid


class SyncNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...

    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


@dataclass(frozen=True)
class SyncResult:
    new_posts: int = 0
    matched: int = 0
    skipped: str | None = None  # mode_off | paused | auth


@dataclass(frozen=True)
class SnapshotResult:
    taken: bool
    outcome_basis: str | None = None
    evidence: int = 0


def _minutes(since: datetime | None, now: datetime) -> int:
    return 0 if since is None else max(0, int((now - since).total_seconds() // 60))


class OwnSync:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        x_for: Callable[[UUID], Awaitable[XClient]],
        queue: JobQueue,
        matcher: PublicationMatcher | None,
        brain: BrainDeps | None,
        notifier: SyncNotifier,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._x_for = x_for
        self._queue = queue
        self._matcher = matcher
        self._brain = brain
        self._notifier = notifier

    # --- синк ----------------------------------------------------------------------------

    async def sync_own_posts(self, account_id: UUID) -> SyncResult:
        async with self._uow() as uow:
            account = await AccountsRepo(uow.session).get(account_id)
            if account is None:
                raise NotFound(f"аккаунт {account_id} не найден")
            mode, paused = account.x_read_mode, bool(account.settings.get(PAUSED_SETTING))
            since = await self._since_id(uow.session, account_id)
        if mode in NO_READ_MODES:
            return SyncResult(skipped="mode_off")
        if paused:
            return SyncResult(skipped="paused")
        try:
            client = await self._x_for(account_id)
            posts = await client.get_own_posts(since, OWN_POSTS_LIMIT)
        except XAuthError:
            await self._pause(account_id)
            return SyncResult(skipped="auth")
        except XModeError:
            return SyncResult(skipped="mode_off")
        stored = [p for p in reversed(posts) if await self._store(account_id, p)]
        matches = await self._match(account_id, posts)
        await self._flag_bot_assisted(account_id)
        now = self._clock.now()
        for post in stored:
            if post.created_at is not None and now - post.created_at < timedelta(days=7):
                await self.schedule_checkpoints(account_id, post.id, post.created_at)
        async with self._uow() as uow:
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.METRICS_SYNCED,
                props={"posts": len(stored)},
            )
        return SyncResult(new_posts=len(stored), matched=sum(m.outcome == "auto" for m in matches))

    @staticmethod
    async def _since_id(session: DbSession, account_id: UUID) -> str | None:
        return await AnalyticsRepo(session, account_id).max_own_post_id()

    async def _pause(self, account_id: UUID) -> None:
        async with self._uow() as uow:
            await AccountsRepo(uow.session).update_settings(account_id, **{PAUSED_SETTING: True})
        await self._notifier.notify_owner(account_id, RECONNECT_TEXT)

    async def _store(self, account_id: UUID, post: XPost) -> bool:
        """Новый свой пост → материал, сигнал, снимок, задачи; уже известный — False."""
        now = self._clock.now()
        async with self._uow() as uow:
            sources = SourcesRepo(uow.session, account_id)
            if await sources.by_external_id(post.id) is not None:
                return False
            kind = "reply" if post.in_reply_to_id else "post"
            src, created = await sources.add_or_get(
                kind=kind,
                stance="own",
                origin="x_api",
                content_hash=hashlib.sha256(f"x:{post.id}".encode()).hexdigest(),
                external_id=post.id,
                author_handle=post.author_handle,
                posted_at=post.created_at,
                status="parsed",
                analysis_status="pending",
                meta={"x_sync": True},
            )
            if not created:
                return False
            await sources.add_document(src.id, text=post.text, text_clean=post.text)
            await self._record_metrics(uow.session, account_id, src.id, post, post.created_at, now)
            jobs = JobsRepo(uow.session)
            for kind_name in (INGEST_KIND, ANALYZE_KIND):
                payload = {"source_id": str(src.id)}
                await jobs.enqueue(
                    account_id=account_id,
                    kind=kind_name,
                    payload=payload,
                    run_at=now,
                    max_attempts=5,
                    idempotency_key=f"{kind_name}:{src.id}",
                )
        return True

    @staticmethod
    async def _record_metrics(
        session: DbSession,
        account_id: UUID,
        source_id: UUID,
        post: XPost,
        created: datetime | None,
        now: datetime,
    ) -> bool:
        """Строка снимка и сигнал `own_sync`; без метрик в ответе X — ничего."""
        m = post.metrics
        if m is None:
            return False
        age = _minutes(created, now)
        await AnalyticsRepo(session, account_id).insert_snapshot(
            x_post_id=post.id,
            ts=now,
            age_minutes=age,
            impressions=m.impressions,
            likes=m.likes,
            replies=m.replies,
            reposts=m.reposts,
            quotes=m.quotes,
            bookmarks=m.bookmarks,
            url_clicks=m.url_clicks,
            profile_clicks=m.profile_clicks,
        )
        await signals.add_signal(
            session,
            account_id,
            source_id,
            signals.Signal(
                origin="own_sync",
                views=m.impressions,
                likes=m.likes,
                replies=m.replies,
                reposts=m.reposts,
                quotes=m.quotes,
                bookmarks=m.bookmarks,
                post_age_hours=age / 60,
                raw={"x_post_id": post.id},
            ),
            observed_at=now,
        )
        return True

    async def _match(self, account_id: UUID, posts: list[XPost]) -> list[Match]:
        """Автосверка после синка; неоднозначные совпадения — вопросом владельцу."""
        if self._matcher is None:
            return []
        from xcopilot.bot import texts
        from xcopilot.review.jobs import match_keyboard

        matches = await self._matcher.match_publications(account_id, posts)
        by_id = {p.id: p for p in posts}
        for m in matches:
            if m.outcome == "ask" and m.x_post_id in by_id:
                post = by_id[m.x_post_id]
                await self._notifier.notify(
                    account_id,
                    f"{texts.MATCH_ASK}\n\n{post.text}\n{m.url}",
                    match_keyboard(m.draft_id, m.x_post_id),
                )
        return matches

    async def _flag_bot_assisted(self, account_id: UUID) -> None:
        """Своим постам без пометки: «с ботом», если пост связан с публикацией, иначе «без»."""
        async with self._uow() as uow:
            await AnalyticsRepo(uow.session, account_id).flag_bot_assisted()

    # --- контрольные снимки ---------------------------------------------------------------

    async def schedule_checkpoints(
        self, account_id: UUID, x_post_id: str, published_at: datetime
    ) -> list[UUID]:
        """Задачи `analytics.snapshot` на 1ч, 24ч, 72ч и 7д после публикации (идемпотентно)."""
        ids = []
        for minutes in CHECKPOINT_MINUTES:
            ids.append(
                await self._queue.enqueue(
                    SNAPSHOT_KIND,
                    {"x_post_id": x_post_id, "checkpoint_min": minutes},
                    account_id=account_id,
                    run_at=published_at + timedelta(minutes=minutes),
                    idempotency_key=f"{SNAPSHOT_KIND}:{x_post_id}:{minutes}",
                )
            )
        return ids

    async def take_snapshot(
        self, account_id: UUID, x_post_id: str, checkpoint_min: int | None = None
    ) -> SnapshotResult:
        """Снимок метрик поста; на отметке 24 часа — исход и доказательства знаниям."""
        async with self._uow() as uow:
            account = await AccountsRepo(uow.session).get(account_id)
            if account is None:
                raise NotFound(f"аккаунт {account_id} не найден")
            handle = account.handle
            blocked = account.x_read_mode in NO_READ_MODES or bool(
                account.settings.get(PAUSED_SETTING)
            )
            source = await SourcesRepo(uow.session, account_id).by_external_id(x_post_id)
        if blocked or source is None:
            return SnapshotResult(False)
        try:
            client = await self._x_for(account_id)
            post = await client.get_post(PostRef(x_post_id, handle))
        except XAuthError:
            await self._pause(account_id)
            return SnapshotResult(False)
        except (XNotFound, XModeError):
            return SnapshotResult(False)
        now = self._clock.now()
        async with self._uow() as uow:
            taken = await self._record_metrics(
                uow.session, account_id, source.id, post, post.created_at or source.posted_at, now
            )
            basis: str | None = None
            written = 0
            if taken and checkpoint_min == OUTCOME_CHECKPOINT:
                out = await outcomes.compute_outcome(uow.session, account_id, source.id, now=now)
                basis = out.basis
                if self._brain is not None and not out.deferred:
                    ctx = self._brain.ctx(uow.session, account_id)
                    written = await record_own_outcome(ctx, source.id)
                    if written:
                        await recompute(ctx)
        return SnapshotResult(taken, basis, written)
