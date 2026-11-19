"""Решения владельца по черновику (ТЗ, этап 12, задачи 4–8, 14).

Каждое решение — строка `reviews`, событие `review.*` (метрики zero_edit_rate, approval_rate,
отказы по причинам) и доказательства совместимости применённых знаний
(`brain.evidence.record_from_review`, раздел Б2.4). Повторное нажатие не создаёт второго
решения (строка черновика блокируется), устаревшая карточка — `CardExpired`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from rapidfuzz.distance import Levenshtein

from xcopilot.brain.config import BrainSettings, load_brain_config
from xcopilot.brain.context import BrainContext
from xcopilot.brain.evidence import record_from_review
from xcopilot.clock import Clock
from xcopilot.db.repos import DraftsRepo, ReviewsRepo, RunsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.review import ReviewQueries
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.jobs.queue import JobQueue
from xcopilot.metrics.events import EventType, emit
from xcopilot.quality.lint.engine import Finding, LintReport
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.review.callbacks import REGEN_HINTS, REGEN_OWN, REJECT_REASONS
from xcopilot.review.expiry import OPEN, expire_run, run_expired
from xcopilot.review.publish import parts_for
from xcopilot.review.settings import PUBLICATIONS_MATCH_KIND, ReviewSettings
from xcopilot.review.translate import Translator
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, generate_payload, tail_payload
from xcopilot.writer.schemas import TargetPost
from xcopilot.writer.trend_tail import PostRef


class CardExpired(Exception):
    """Карточка старше 48 часов: кнопки больше не действуют."""


class Relinter(Protocol):
    """Повторный прогон линтера по исправленному тексту (QualityGate.relint)."""

    async def relint(
        self, account_id: UUID, draft_id: UUID, text: str, parts: Sequence[str]
    ) -> LintReport: ...


@dataclass(frozen=True)
class DecisionResult:
    draft_id: UUID
    status: str
    review_id: UUID | None = None
    repeated: bool = False  # решение уже было: ничего не записано
    distance: float | None = None
    warnings: tuple[str, ...] = ()  # ошибки линтера правки — как предупреждения
    translation: tuple[str, ...] = ()  # перевод правки на русский (если нужен и удался)


def edit_distance(before: str, after: str) -> float:
    """Нормированная дистанция Левенштейна 0..1."""
    return float(Levenshtein.normalized_distance(before, after))


def hint_text(hint: str | None) -> tuple[str | None, str | None]:
    """Подсказка «Ещё вариант» → (текст для писателя, код причины)."""
    if hint is None:
        return None, None
    if hint in REGEN_HINTS:
        return REGEN_HINTS[hint], hint
    text = hint.strip()
    return (text or None), REGEN_OWN


def match_schedule(decided_at: datetime, ttl: timedelta) -> list[datetime]:
    """Когда сверить ленту после решения: через 3 и 12 часов и сразу после окна."""
    return [
        decided_at + timedelta(hours=3),
        decided_at + timedelta(hours=12),
        decided_at + ttl + timedelta(hours=1),
    ]


class Decisions:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        queue: JobQueue,
        *,
        relinter: Relinter | None = None,
        settings: ReviewSettings | None = None,
        brain_config: BrainSettings | None = None,
        translator: Translator | None = None,
    ) -> None:
        self._translator = translator
        self._uow = uow_factory
        self._clock = clock
        self._queue = queue
        self._relinter = relinter
        self._settings = settings or ReviewSettings()
        self._brain_config = brain_config or load_brain_config()

    # ── решения ────────────────────────────────────────────────────────────────────────

    async def approve(
        self, account_id: UUID, draft_id: UUID, user_id: UUID, *, comment: str | None = None
    ) -> DecisionResult:
        await self._check_expired(account_id, draft_id)
        now = self._clock.now()
        async with self._uow() as uow:
            draft = await self._lock(uow.session, account_id, draft_id)
            if draft.status != DraftStatus.SHOWN:
                return DecisionResult(draft_id, draft.status, repeated=True)
            await DraftsRepo(uow.session, account_id).set_status(draft_id, DraftStatus.APPROVED)
            review = await self._review(
                uow.session, account_id, draft, user_id, "approve", now, comment=comment
            )
            await self._emit(
                uow.session, account_id, draft_id, user_id, EventType.REVIEW_APPROVED, {}, now
            )
            await self._evidence(uow.session, account_id, review)
        await self._schedule_match(account_id, draft_id, now)
        return DecisionResult(draft_id, DraftStatus.APPROVED.value, review_id=review)

    async def edit(
        self,
        account_id: UUID,
        draft_id: UUID,
        user_id: UUID,
        new_text: str,
        *,
        comment: str | None = None,
        over_approved: bool = False,
    ) -> DecisionResult:
        """Правка. `over_approved` — поверх выбора кнопкой «✅ Выбрать» (R5): approved → edited."""
        text = new_text.strip()
        if not text:
            raise ValueError("правка: пустой текст")
        await self._check_expired(account_id, draft_id)
        async with self._uow() as uow:
            row = await DraftsRepo(uow.session, account_id).require(draft_id)
            fmt = row.format
            before = await ReviewQueries(uow.session, account_id).last_edit_text(draft_id)
        parts = parts_for(fmt, text)
        warnings: tuple[str, ...] = ()
        if self._relinter is not None:
            report = await self._relinter.relint(account_id, draft_id, text, parts)
            warnings = tuple(_warning(f) for f in report.errors)
        now = self._clock.now()
        async with self._uow() as uow:
            draft = await self._lock(uow.session, account_id, draft_id)
            allowed: tuple[DraftStatus, ...] = (DraftStatus.SHOWN, DraftStatus.EDITED)
            if over_approved:
                allowed = (*allowed, DraftStatus.APPROVED)
            if draft.status not in allowed:
                return DecisionResult(draft_id, draft.status, repeated=True)
            original = before if before is not None else draft.text
            distance = round(edit_distance(original, text), 4)
            first = draft.status in (DraftStatus.SHOWN, DraftStatus.APPROVED)
            if first:
                await DraftsRepo(uow.session, account_id).set_status(draft_id, DraftStatus.EDITED)
            review = await self._review(
                uow.session,
                account_id,
                draft,
                user_id,
                "edit",
                now,
                text_before=original,
                text_after=text,
                edit_distance=distance,
                comment=comment,
            )
            if first:  # повторная правка уточняет то же решение: событие одно на черновик
                await self._emit(
                    uow.session,
                    account_id,
                    draft_id,
                    user_id,
                    EventType.REVIEW_EDITED,
                    {"edit_distance": distance},
                    now,
                )
            await self._evidence(uow.session, account_id, review)
        if first:
            await self._schedule_match(account_id, draft_id, now)
        translation: tuple[str, ...] = ()
        if self._translator is not None:  # правка меняет оригинал — перевод обновляется
            ru = await self._translator.refresh(account_id, draft_id, text, parts)
            translation = tuple(ru or ())
        return DecisionResult(
            draft_id, DraftStatus.EDITED.value, review, False, distance, warnings, translation
        )

    async def reject(
        self,
        account_id: UUID,
        draft_id: UUID,
        user_id: UUID,
        reason: str,
        comment: str | None = None,
    ) -> DecisionResult:
        if reason not in REJECT_REASONS:
            raise ValueError(f"отказ: нужна причина из {sorted(REJECT_REASONS)}")
        await self._check_expired(account_id, draft_id)
        now = self._clock.now()
        async with self._uow() as uow:
            draft = await self._lock(uow.session, account_id, draft_id)
            if draft.status not in (DraftStatus.SHOWN, DraftStatus.NEEDS_OWNER):
                return DecisionResult(draft_id, draft.status, repeated=True)
            await DraftsRepo(uow.session, account_id).set_status(draft_id, DraftStatus.REJECTED)
            review = await self._review(
                uow.session,
                account_id,
                draft,
                user_id,
                "reject",
                now,
                reason_code=reason,
                comment=(comment or None),
            )
            await self._emit(
                uow.session,
                account_id,
                draft_id,
                user_id,
                EventType.REVIEW_REJECTED,
                {"reason_code": reason},
                now,
            )
            await self._evidence(uow.session, account_id, review)
        return DecisionResult(draft_id, DraftStatus.REJECTED.value, review_id=review)

    async def regenerate(
        self, account_id: UUID, draft_id: UUID, user_id: UUID, hint: str | None = None
    ) -> UUID:
        """Новая генерация по той же задаче с подсказкой; вернуть id задачи writer.generate."""
        await self._check_expired(account_id, draft_id)
        text, code = hint_text(hint)
        now = self._clock.now()
        async with self._uow() as uow:
            draft = await self._lock(uow.session, account_id, draft_id)
            run = await RunsRepo(uow.session, account_id).require(draft.run_id)
            payload = _regen_payload(run.kind, dict(run.context_pack or {}), run.topic_id, text)
            review = await self._review(
                uow.session, account_id, draft, user_id, "regenerate", now, reason_code=code
            )
            await self._emit(
                uow.session,
                account_id,
                draft_id,
                user_id,
                EventType.REVIEW_REGENERATE,
                {"reason_code": code},
                now,
            )
        return await self._queue.enqueue(
            WRITER_GENERATE_KIND, payload, account_id=account_id, idempotency_key=f"regen:{review}"
        )

    # ── шаги ────────────────────────────────────────────────────────────────────────────

    async def _check_expired(self, account_id: UUID, draft_id: UUID) -> None:
        """Устаревшая карточка: пометить (отдельной транзакцией) и бросить CardExpired."""
        now = self._clock.now()
        async with self._uow() as uow:
            draft = await DraftsRepo(uow.session, account_id).require(draft_id)
            if draft.status == DraftStatus.EXPIRED:
                raise CardExpired(str(draft_id))
            if draft.status not in OPEN:
                return
            if not await run_expired(
                uow.session, account_id, draft.run_id, now, self._settings.card_ttl
            ):
                return
            await expire_run(uow.session, account_id, draft.run_id)
        raise CardExpired(str(draft_id))

    @staticmethod
    async def _lock(session: DbSession, account_id: UUID, draft_id: UUID) -> Any:
        draft = await ReviewQueries(session, account_id).lock_draft(draft_id)
        if draft is None:
            raise NotFound(f"черновик {draft_id} не найден")
        return draft

    @staticmethod
    async def _review(
        session: DbSession,
        account_id: UUID,
        draft: Any,
        user_id: UUID,
        action: str,
        now: datetime,
        **values: Any,
    ) -> UUID:
        values.setdefault("text_before", draft.text)
        row = await ReviewsRepo(session, account_id).add(
            draft_id=draft.id, user_id=user_id, action=action, created_at=now, **values
        )
        return row.id

    @staticmethod
    async def _emit(
        session: DbSession,
        account_id: UUID,
        draft_id: UUID,
        user_id: UUID,
        type_: EventType,
        props: dict[str, Any],
        now: datetime,
    ) -> None:
        await emit(
            session,
            account_id=account_id,
            type=type_,
            actor="user",
            user_id=user_id,
            entity=("draft", draft_id),
            props=props,
            ts=now,
        )

    async def _evidence(self, session: DbSession, account_id: UUID, review_id: UUID) -> None:
        ctx = BrainContext(session, account_id, self._clock, config=self._brain_config)
        await record_from_review(ctx, review_id)

    async def _schedule_match(self, account_id: UUID, draft_id: UUID, decided_at: datetime) -> None:
        for n, at in enumerate(match_schedule(decided_at, self._settings.match_window), 1):
            await self._queue.enqueue(
                PUBLICATIONS_MATCH_KIND,
                {},
                account_id=account_id,
                run_at=at,
                idempotency_key=f"pubmatch:{draft_id}:{n}",
            )


def _warning(finding: Finding) -> str:
    return f"{finding.message}: «{finding.fragment}»" if finding.fragment else finding.message


def _regen_payload(
    kind: str, context: dict[str, Any], topic_id: UUID | None, hint: str | None
) -> dict[str, Any]:
    trend = context.get("trend")
    if kind == "trend_tail" and isinstance(trend, dict) and trend.get("url"):
        return tail_payload(PostRef(url=trend["url"]), own_topic=trend.get("own_topic"))
    target = None
    if context.get("target_text"):
        target = TargetPost(text=context["target_text"], url=context.get("target_url"))
    task = TaskSpec.model_validate(context["task"])
    return generate_payload(task, hint=hint, topic_id=topic_id, target=target)
