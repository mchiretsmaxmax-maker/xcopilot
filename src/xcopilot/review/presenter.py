"""Показ карточек владельцу (ТЗ, этап 12, задачи 1, 3, 8, 11).

Карточка запуска уходит после решения ворот качества (этап 11) по финальным ревизиям
вариантов. Статус `shown` и событие `card.shown` пишутся только после успешной отправки.
Сверх дневного предела карточка ждёт в /queue. Навигация ◀ ▶ редактирует одно сообщение.
Карточка старше 48 часов — `expired`: кнопки сняты, решения по ней не принимаются.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from aiogram import Bot

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, DraftsRepo, RunsRepo, TopicsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.review import ReviewCardsRepo, ReviewQueries
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.metrics.events import EventType, emit
from xcopilot.quality.loop import restore_decision
from xcopilot.review.cards import CardDraft, CardView, WhyItem, render_card, render_why
from xcopilot.review.expiry import OPEN, expire_run, run_expired
from xcopilot.review.settings import ReviewSettings

log = structlog.get_logger()
HTML = "HTML"


@dataclass(frozen=True)
class QueuedCard:
    run_id: UUID
    draft_id: UUID  # первый вариант — для кнопки «Показать»
    title: str
    created_at: datetime


class CardPresenter:
    def __init__(
        self,
        bot: Bot,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        settings: ReviewSettings | None = None,
    ) -> None:
        self._bot = bot
        self._uow = uow_factory
        self._clock = clock
        self._settings = settings or ReviewSettings()

    async def show_for_drafts(self, account_id: UUID, draft_ids: Sequence[UUID]) -> int:
        """После ворот: показать карточки запусков этих черновиков; вернуть число отправленных."""
        runs: list[UUID] = []
        async with self._uow() as uow:
            drafts = DraftsRepo(uow.session, account_id)
            for draft_id in draft_ids:
                row = await drafts.get(draft_id)
                if row is not None and row.run_id not in runs:
                    runs.append(row.run_id)
        sent = 0
        for run_id in runs:
            sent += await self.show_run(account_id, run_id)
        return sent

    async def show_run(
        self, account_id: UUID, run_id: UUID, *, chat_id: int | None = None, force: bool = False
    ) -> int:
        """Карточка запуска владельцам (или одному чату); force — мимо дневного предела."""
        now = self._clock.now()
        async with self._uow() as uow:
            session = uow.session
            loaded = await _load_run(session, account_id, run_id)
            if loaded is None:
                return 0
            run, finals = loaded
            if not any(DraftStatus(d.status) in WAITING for d in finals.values()):
                return 0  # уже показана или решена
            cards = ReviewCardsRepo(session, account_id)
            existing = {c.chat_id: c for c in await cards.by_run(run_id)}
            if any(c.state != "queued" for c in existing.values()):
                return 0  # повтор задачи ворот: карточка уже ушла
            chats = (
                [chat_id]
                if chat_id is not None
                else await AccountsRepo(session).member_tg_ids(account_id, ("owner",))
            )
            if not force and await self._limit_reached(session, account_id, now):
                for chat in chats:
                    if chat not in existing:
                        await cards.add(run_id=run_id, chat_id=chat, state="queued")
                log.info("card_queued", run_id=str(run_id), reason="daily_limit")
                return 0
            first = finals[min(finals)]
            view = render_card(_card_draft(run, finals, first), restore_decision(first))
        sent_to: list[tuple[int, int]] = []
        for chat in chats:
            message_id = await self._send(chat, view)
            if message_id is not None:
                sent_to.append((chat, message_id))
        if not sent_to:
            return 0
        async with self._uow() as uow:
            session = uow.session
            cards = ReviewCardsRepo(session, account_id)
            for chat, message_id in sent_to:
                values = {
                    "message_id": message_id,
                    "state": "shown",
                    "shown_at": now,
                    "variant_no": first.variant_no,
                }
                card = await cards.for_chat(run_id, chat)
                if card is None:
                    await cards.add(run_id=run_id, chat_id=chat, **values)
                else:
                    await cards.update(card.id, **values)
            drafts = DraftsRepo(session, account_id)
            for d in finals.values():
                if DraftStatus(d.status) in WAITING:
                    await drafts.set_status(d.id, DraftStatus.SHOWN)
            await emit(
                session,
                account_id=account_id,
                type=EventType.CARD_SHOWN,
                entity=("run", run_id),
                props={"kind": run.kind},
                ts=now,
            )
        return len(sent_to)

    async def navigate(
        self, account_id: UUID, draft_id: UUID, variant_no: int, chat_id: int, message_id: int
    ) -> bool:
        """Показать вариант variant_no в том же сообщении; False — карточка устарела."""
        if await self.is_expired(account_id, draft_id):
            return False
        async with self._uow() as uow:
            session = uow.session
            draft = await DraftsRepo(session, account_id).require(draft_id)
            loaded = await _load_run(session, account_id, draft.run_id)
            if loaded is None or variant_no not in loaded[1]:
                return True
            run, finals = loaded
            row = finals[variant_no]
            view = render_card(_card_draft(run, finals, row), restore_decision(row))
            cards = ReviewCardsRepo(session, account_id)
            card = await cards.for_chat(run.id, chat_id)
            if card is not None:
                await cards.update(card.id, variant_no=variant_no, message_id=message_id)
        for chunk in view.before:
            await self._bot.send_message(chat_id, chunk, parse_mode=HTML)
        await self._bot.edit_message_text(
            text=view.text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=view.keyboard,
            parse_mode=HTML,
        )
        return True

    async def is_expired(self, account_id: UUID, draft_id: UUID) -> bool:
        """Карточка черновика старше срока (и пометить её expired)."""
        now = self._clock.now()
        async with self._uow() as uow:
            draft = await DraftsRepo(uow.session, account_id).require(draft_id)
            if draft.status == DraftStatus.EXPIRED:
                return True
            if not await run_expired(
                uow.session, account_id, draft.run_id, now, self._settings.card_ttl
            ):
                return False
            if DraftStatus(draft.status) not in OPEN:
                return False  # решённый вариант: публикация и ссылки работают и позже
            await expire_run(uow.session, account_id, draft.run_id)
        return True

    async def disable(self, chat_id: int, message_id: int) -> None:
        """Снять кнопки с сообщения карточки (ошибки Telegram не мешают)."""
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=None
            )
        except Exception:
            log.info("card_disable_failed", chat_id=chat_id, message_id=message_id, exc_info=True)

    async def expire_cards(self, account_id: UUID) -> int:
        """Задача cards.expire: карточки старше срока → expired, кнопки сняты; вернуть число."""
        cutoff = self._clock.now() - self._settings.card_ttl
        messages: list[tuple[int, int]] = []
        async with self._uow() as uow:
            stale = await ReviewCardsRepo(uow.session, account_id).stale(cutoff, cutoff)
            runs = list(dict.fromkeys(c.run_id for c in stale))
            for run_id in runs:
                messages += await expire_run(uow.session, account_id, run_id)
        for chat_id, message_id in dict.fromkeys(messages):
            await self.disable(chat_id, message_id)
        return len(runs)

    async def queued(self, account_id: UUID) -> list[QueuedCard]:
        """Карточки, ждущие показа (дневной предел)."""
        out: list[QueuedCard] = []
        async with self._uow() as uow:
            for card in await ReviewCardsRepo(uow.session, account_id).queued():
                if any(q.run_id == card.run_id for q in out):
                    continue
                loaded = await _load_run(uow.session, account_id, card.run_id)
                if loaded is None:
                    continue
                run, finals = loaded
                first = finals[min(finals)]
                title = _topic(run) or first.text[:60]
                out.append(QueuedCard(run.id, first.id, title, card.created_at))
        return out

    async def why(self, account_id: UUID, draft_id: UUID) -> str:
        """«Почему так» по варианту: знания пакета и отметки применения."""
        async with self._uow() as uow:
            draft = await DraftsRepo(uow.session, account_id).require(draft_id)
            run = await RunsRepo(uow.session, account_id).require(draft.run_id)
            context = dict(run.context_pack or {})
            rows = await ReviewQueries(uow.session, account_id).applied_rows(draft_id)
        items = [WhyItem(r.guidance, r.slot, r.applied) for r in rows]
        probation = _probation(context) if draft.probation_item_id else None
        return render_why(items, probation)

    # ── шаги ────────────────────────────────────────────────────────────────────────────

    async def _limit_reached(self, session: DbSession, account_id: UUID, now: datetime) -> bool:
        account = await AccountsRepo(session).get(account_id)
        tz = _zone(account.tz if account is not None else "UTC")
        local = now.astimezone(tz)
        day_start = datetime.combine(local.date(), time(0), tzinfo=tz).astimezone(UTC)
        shown = await ReviewCardsRepo(session, account_id).shown_runs_since(day_start)
        return shown >= self._settings.daily_card_limit

    async def _send(self, chat_id: int, view: CardView) -> int | None:
        """Отправить карточку; вернуть id сообщения с кнопками или None при ошибке Telegram."""
        try:
            for chunk in view.before:
                await self._bot.send_message(chat_id, chunk, parse_mode=HTML)
            message = await self._bot.send_message(
                chat_id, view.text, reply_markup=view.keyboard, parse_mode=HTML
            )
        except Exception:
            log.warning("card_send_failed", chat_id=chat_id, exc_info=True)
            return None
        return message.message_id


# статусы финальной ревизии, когда карточка ещё не показана
WAITING = (DraftStatus.JUDGED, DraftStatus.NEEDS_OWNER)


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class _Run:
    id: UUID
    kind: str
    context: dict[str, Any]
    topic: str | None


async def _load_run(
    session: DbSession, account_id: UUID, run_id: UUID
) -> tuple[_Run, dict[int, Any]] | None:
    """Запуск (с темой) и финальные ревизии его вариантов по variant_no."""
    row = await RunsRepo(session, account_id).get(run_id)
    if row is None:
        return None
    finals: dict[int, Any] = {}
    for d in await DraftsRepo(session, account_id).list_by_run(run_id):
        if d.variant_no not in finals or d.revision > finals[d.variant_no].revision:
            finals[d.variant_no] = d
    if not finals:
        return None
    context = dict(row.context_pack or {})
    title = (context.get("task") or {}).get("topic") or None
    if row.topic_id is not None:
        topic = await TopicsRepo(session, account_id).get(row.topic_id)
        if topic is not None:
            title = topic.title
    return _Run(row.id, row.kind, context, title), dict(sorted(finals.items()))


def _topic(run: _Run) -> str | None:
    return run.topic


def _probation(context: dict[str, Any]) -> str | None:
    probation = context.get("probation") or {}
    return probation.get("guidance") or None


def _card_draft(run: _Run, finals: dict[int, Any], row: Any) -> CardDraft:
    return CardDraft(
        draft_id=row.id,
        variant_no=row.variant_no,
        total=len(finals),
        format=row.format,
        text=row.text,
        parts=list(row.parts or []),
        topic=_topic(run),
        probation=_probation(run.context) if row.probation_item_id else None,
    )
