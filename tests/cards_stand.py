"""Стенд этапа 12: писатель + ворота + настоящий воркер + бот на FakeTelegram + FakeX.

Полный путь: задача `writer.generate` → `quality.gate` → карточка владельцу. Аккаунт `acc`,
владелец — пользователь Telegram OWNER (как после онбординга).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from aiogram import Dispatcher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram, FakeX
from tests.fakes.telegram import SentMessage, callback_update
from tests.integration.bot.conftest import OWNER, Client, make_onboarded
from tests.quality_stand import GateStand, good, make_gate_stand
from tests.writer_stand import DISTINCT, draft_reply, post_task
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.jobs.handlers import JobDeps, build_registry
from xcopilot.jobs.queue import JobQueue
from xcopilot.jobs.worker import Worker
from xcopilot.review.callbacks import card_cb
from xcopilot.review.kit import ReviewKit, build_review
from xcopilot.review.settings import ReviewSettings
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, generate_payload
from xcopilot.x.models import XPost

SF = async_sessionmaker[AsyncSession]
HANDLE = "acc"
_ids = itertools.count(50_000)


@dataclass
class CardsStand:
    gs: GateStand
    tg: FakeTelegram
    deps: BotDeps
    kit: ReviewKit
    x: FakeX
    owner: Client
    worker: Worker
    user_id: UUID

    @property
    def account_id(self) -> UUID:
        return self.gs.account_id

    @property
    def queue(self) -> JobQueue:
        return self.gs.ws.queue

    @property
    def clock(self) -> Any:
        return self.gs.ws.clock

    def advance_hours(self, hours: float) -> None:
        self.clock.advance(hours * 3600)

    async def generate(
        self,
        *texts: str,
        topic: str = "дешёвые комиссии в l2",
        fmt: str = "post",
        parts: list[list[str]] | None = None,
        hint: str | None = None,
        reply_to: str | None = None,
    ) -> UUID:
        """Поставить writer.generate (как бот) и прогнать очередь: писатель → ворота → карточка.

        Вернуть id запуска. Судья по умолчанию пропускает каждый вариант.
        """
        texts = texts or tuple(DISTINCT)
        self.gs.ws.writer.push(draft_reply(*texts, parts=parts))
        self.gs.judge.push(*[good() for _ in texts])
        task = post_task(topic, fmt=fmt)
        target = None
        if reply_to is not None:
            from xcopilot.writer.schemas import TargetPost

            target = TargetPost(text="целевой пост про газ", url=reply_to, author="someone")
        await self.queue.enqueue(
            WRITER_GENERATE_KIND,
            generate_payload(task, hint=hint, target=target),
            account_id=self.account_id,
        )
        await self.drain()
        rows = await self.rows(
            "SELECT id FROM generation_runs WHERE account_id = :a ORDER BY created_at DESC, id DESC"
            " LIMIT 1",
            a=self.account_id,
        )
        return cast(UUID, rows[0][0])

    async def drain(self) -> int:
        return await self.worker.drain()

    async def finals(self, run_id: UUID) -> list[UUID]:
        """Финальные ревизии вариантов запуска по порядку variant_no."""
        rows = await self.rows(
            "SELECT DISTINCT ON (variant_no) id FROM drafts WHERE run_id = :r"
            " ORDER BY variant_no, revision DESC",
            r=run_id,
        )
        return [r[0] for r in rows]

    async def status(self, draft_id: UUID) -> str:
        return cast(
            str, (await self.rows("SELECT status FROM drafts WHERE id = :d", d=draft_id))[0][0]
        )

    def cards(self) -> list[SentMessage]:
        """Сообщения-карточки владельцу (с кнопкой «✅ Одобрить»)."""
        return [m for m in self.tg.sent if m.chat_id == OWNER and "✅ Одобрить" in m.buttons]

    async def press(self, draft_id: UUID, action: str, arg: str = "", *, message_id: int = 0,
                    who: Client | None = None) -> list[SentMessage]:  # fmt: skip
        client = who or self.owner
        update = callback_update(
            next(_ids), client.user_id, card_cb(draft_id, action, arg), message_id or None
        )
        return await client.feed(update)

    def own_post(self, post_id: str, body: str, *, at: datetime | None = None,
                 handle: str = HANDLE) -> XPost:  # fmt: skip
        post = XPost(
            id=post_id,
            text=body,
            author_handle=handle,
            created_at=at or self.clock.now(),
            source="oembed",
        )
        self.x.add_post(post, own=handle == HANDLE)
        return post

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        return await self.gs.rows(sql, **params)

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        return await self.gs.count(table, where, **params)


async def make_cards_stand(
    sf: SF,
    telegram: FakeTelegram,
    deps: BotDeps,
    *,
    settings: ReviewSettings | None = None,
) -> CardsStand:
    gs = await make_gate_stand(sf, handle=HANDLE)
    acc = await make_onboarded(sf, handle=HANDLE)
    assert acc == gs.account_id
    ws = gs.ws
    x = FakeX()

    async def x_for(_: UUID) -> Any:
        return x

    deps.queue = ws.queue
    deps.clock = ws.clock
    kit = build_review(
        bot=telegram.bot,
        uow_factory=ws.uow,
        clock=ws.clock,
        queue=ws.queue,
        x_for=x_for,
        relinter=gs.gate,
        settings=settings,
    )
    deps.review = kit
    notifier = Notifier(telegram.bot, ws.uow)
    unused = cast(Any, None)
    registry = build_registry(
        JobDeps(
            uow_factory=ws.uow,
            clock=ws.clock,
            queue=ws.queue,
            ingest=unused,
            analyzer=unused,
            importer=unused,
            writer=ws.service,
            topics=ws.topics,
            brain=unused,
            x_for=x_for,
            notifier=notifier,
            bot=deps,
            quality=gs.gate,
            review=kit,
        )
    )
    dp: Dispatcher = build_dispatcher(deps)
    async with sf() as s:
        user_id = (
            await s.execute(text("SELECT id FROM users WHERE tg_user_id = :t"), {"t": OWNER})
        ).scalar_one()
    return CardsStand(
        gs=gs,
        tg=telegram,
        deps=deps,
        kit=kit,
        x=x,
        owner=Client(dp, telegram, OWNER),
        worker=Worker(ws.queue, registry, worker_id="test"),
        user_id=user_id,
    )
