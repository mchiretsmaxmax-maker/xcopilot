"""Стенд этапа 14: «Мозг»-стенд 7A + OwnSync + очередь + подделки X и модели отчётов.

Свои посты кладутся в FakeX (`own_post`), публикации бота — SQL-вставкой (`publish`).
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import text

from tests.brain_stand import BrainStand, make_brain_stand
from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.x import FakeNotifier
from tests.ingest_stand import RecordingNotifier
from xcopilot.analytics.sync import OwnSync
from xcopilot.brain.jobs import BrainDeps
from xcopilot.jobs.queue import JobQueue
from xcopilot.llm.roles import Role
from xcopilot.review.matcher import PublicationMatcher
from xcopilot.x.models import XMetrics, XPost

_ids = itertools.count(900_000)


@dataclass
class OwnerNotifier(RecordingNotifier):
    owner_texts: list[str] = field(default_factory=list)

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        self.owner_texts.append(text)


@dataclass
class FakeReportLLM:
    """Шлюз для отчётов: ответы по очереди; `fail` — исключение вместо ответа."""

    results: list[BaseModel | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def push(self, *items: BaseModel | Exception) -> FakeReportLLM:
        self.results.extend(items)
        return self

    async def generate[T: BaseModel](
        self,
        *,
        role: Role,
        prompt_key: str,
        variables: Mapping[str, Any],
        schema: type[T],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> T:
        self.calls.append({"role": role, "prompt_key": prompt_key, "variables": dict(variables)})
        if not self.results:
            raise AssertionError("FakeReportLLM: сценарий закончился")
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return schema.model_validate(item.model_dump())


@dataclass
class AStand:
    b: BrainStand
    queue: JobQueue
    sync: OwnSync
    brain: BrainDeps
    notifier: OwnerNotifier
    report_llm: FakeReportLLM

    @property
    def sf(self) -> Any:
        return self.b.sf

    @property
    def clock(self) -> FakeClock:
        return self.b.clock

    @property
    def account_id(self) -> UUID:
        return self.b.account_id

    @property
    def x(self) -> Any:
        return self.b.x

    def own_post(
        self,
        body: str,
        *,
        age: timedelta = timedelta(hours=2),
        views: int | None = 1000,
        likes: int = 10,
        post_id: str | None = None,
        reply_to: str | None = None,
    ) -> XPost:
        metrics = (
            XMetrics(likes=likes, reposts=1, replies=1, quotes=0, bookmarks=2, impressions=views)
            if views is not None
            else None
        )
        post = XPost(
            id=post_id or str(next(_ids)),
            text=body,
            author_handle="me",
            created_at=self.clock.now() - age,
            source="api",
            metrics=metrics,
            in_reply_to_id=reply_to,
        )
        self.x.add_post(post, own=True)
        return post

    async def publish(self, body: str, post_id: str, published_at: datetime) -> UUID:
        """Черновик → запись publications (как после автосверки)."""
        run = uuid4()
        draft = uuid4()
        async with self.sf() as s:
            await s.execute(
                text(
                    "INSERT INTO generation_runs (id, account_id, kind, status)"
                    " VALUES (:r, :a, 'post', 'done')"
                ),
                {"r": run, "a": self.account_id},
            )
            await s.execute(
                text(
                    "INSERT INTO drafts (id, account_id, run_id, format, text, status)"
                    " VALUES (:d, :a, :r, 'post', :t, 'published')"
                ),
                {"d": draft, "a": self.account_id, "r": run, "t": body},
            )
            await s.execute(
                text(
                    "INSERT INTO publications (account_id, draft_id, x_post_id, url, published_at,"
                    " matched_by, final_text) VALUES (:a, :d, :p, :u, :t, 'auto', :f)"
                ),
                {
                    "a": self.account_id,
                    "d": draft,
                    "p": post_id,
                    "u": f"https://x.com/me/status/{post_id}",
                    "t": published_at,
                    "f": body,
                },
            )
            await s.commit()
        return draft

    async def seed_post(
        self,
        body: str,
        *,
        posted_at: datetime,
        views: int,
        kind: str = "post",
        tags: tuple[str, ...] = (),
        bot: bool = False,
        origin: str = "x_api",
        x_post_id: str | None = None,
        snapshot_age_min: int = 25 * 60,
    ) -> tuple[UUID, str]:
        """Свой пост «как после синка»: источник, документ, кусок с тегами и снимок метрик."""
        xid = x_post_id or str(next(_ids))
        sid, doc, chunk = uuid4(), uuid4(), uuid4()
        async with self.sf() as s:
            await s.execute(
                text(
                    "INSERT INTO sources (id, account_id, kind, stance, origin, content_hash,"
                    " external_id, posted_at, status, analysis_status, meta)"
                    " VALUES (:i, :a, :k, 'own', :o, :h, :x, :p, 'indexed', 'analyzed',"
                    " CAST(:m AS jsonb))"
                ),
                {
                    "i": sid, "a": self.account_id, "k": kind, "o": origin, "h": uuid4().hex,
                    "x": xid, "p": posted_at, "m": json.dumps({"bot_assisted": bot}),
                },
            )  # fmt: skip
            await s.execute(
                text(
                    "INSERT INTO documents (id, account_id, source_id, text, text_clean)"
                    " VALUES (:d, :a, :s, :t, :t)"
                ),
                {"d": doc, "a": self.account_id, "s": sid, "t": body},
            )
            await s.execute(
                text(
                    "INSERT INTO chunks (id, account_id, document_id, idx, text, tags)"
                    " VALUES (:c, :a, :d, 0, :t, :g)"
                ),
                {
                    "c": chunk, "a": self.account_id, "d": doc, "t": body,
                    "g": list(tags),
                },
            )  # fmt: skip
            await s.execute(
                text(
                    "INSERT INTO post_metric_snapshots (id, account_id, x_post_id, ts, age_minutes,"
                    " impressions, likes, replies, reposts, quotes, bookmarks, raw)"
                    " VALUES (:i, :a, :x, :t, :age, :v, :lk, 1, 1, 0, 1, '{}'::jsonb)"
                ),
                {
                    "i": uuid4(), "a": self.account_id, "x": xid,
                    "t": posted_at + timedelta(minutes=snapshot_age_min),
                    "age": snapshot_age_min, "v": views, "lk": views // 50,
                },
            )  # fmt: skip
            await s.commit()
        return sid, xid

    def sync_x_for(self) -> Any:
        return _x_for(self.b)

    async def jobs(self, kind: str) -> list[Any]:
        return await self.b.rows(
            "SELECT payload, run_at, idempotency_key FROM jobs"
            " WHERE account_id = :a AND kind = :k ORDER BY run_at, id",
            a=self.account_id,
            k=kind,
        )

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        return await self.b.rows(sql, **params)

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        return await self.b.count(table, where, **params)

    async def set_account(self, **fields: Any) -> None:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        await self.b.exec(
            f"UPDATE accounts SET {sets} WHERE id = :id", id=self.account_id, **fields
        )


async def make_analytics_stand(sf: Any, clock: FakeClock) -> AStand:
    b = await make_brain_stand(sf, clock, handle="me")
    queue = JobQueue(b.uow, clock)
    brain = BrainDeps(uow_factory=b.uow, clock=clock, embedder=FakeEmbedder())
    notifier = OwnerNotifier()
    matcher = PublicationMatcher(b.uow, clock)
    sync = OwnSync(
        uow_factory=b.uow,
        clock=clock,
        x_for=_x_for(b),
        queue=queue,
        matcher=matcher,
        brain=brain,
        notifier=notifier,
    )
    return AStand(b, queue, sync, brain, notifier, FakeReportLLM())


def _x_for(b: BrainStand) -> Any:
    async def x_for(_: UUID) -> Any:
        return b.x

    return x_for


__all__ = ["AStand", "FakeNotifier", "make_analytics_stand"]
