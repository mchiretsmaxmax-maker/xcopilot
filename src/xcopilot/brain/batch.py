"""Сводка пачки материалов вместо карточки на каждый (feat/auto-outcome, ADR 0033).

Пачка — источники одного приёма (`sources.meta.batch`). Если в пачке больше одного
материала, карточки «🧩 Разбор» после разбора не шлются: когда разобраны все — одна сводка
(исходы, частые хуки и приёмы, кнопки). Не дождались за `SUMMARY_TIMEOUT` — сводка по тому,
что есть, с честным «ещё разбираю N»; опоздавшие приходят своими карточками.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup

from xcopilot.bot import keyboards, texts
from xcopilot.brain import outcomes as brain_outcomes
from xcopilot.brain import taxonomy
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.repos.materials import (
    MaterialAnalysesRepo,
    MaterialOutcomesRepo,
    MaterialSignalsRepo,
)
from xcopilot.db.uow import DbSession, UnitOfWork

SUMMARY_KIND = "brain.batch_summary"
SUMMARY_TIMEOUT = timedelta(minutes=10)
PAGE = 5
TOP = 3
SENT_KEY = "analysis_summary_sent"
DETECT_KIND = "brain.detect"
BATCH_STANCES = ("own", "inspiration")


class Notifier(Protocol):
    async def notify(self, account_id: UUID, text: str, keyboard: Any = None) -> int: ...


@dataclass(frozen=True)
class Summary:
    batch: str
    analyzed: int
    hit: int
    normal: int
    flop: int
    unknown: int
    pending: int
    fresh: int
    hooks: list[tuple[str, int]]
    devices: list[tuple[str, int]]


def bucket(outcome: Any) -> str:
    """Исход материала для сводки: hit | normal | flop | unknown (нет цифр/оценки)."""
    if outcome is None or outcome.basis in ("none", "owner_pick"):
        return "unknown"
    tier = outcome.tier_engagement
    if tier in ("hit", "above"):
        return "hit"
    if tier in ("flop", "below"):
        return "flop"
    return "normal" if tier == "normal" else "unknown"


def _settled(src: Any) -> bool:
    """Материал пачки больше не ждёт разбора (разобран, упал, исключён, убран)."""
    return bool(
        src.status in ("failed", "excluded")
        or src.removed_at is not None
        or src.analysis_status != "pending"
        or (src.meta or {}).get("screen") == "unavailable"
    )


async def _fresh(session: DbSession, account_id: UUID, source_id: UUID, hours: int) -> bool:
    """Без исхода, потому что пост моложе суток (публичные цифры сняты слишком рано)."""
    best = await MaterialSignalsRepo(session, account_id).best(source_id)
    return bool(
        best is not None
        and best.origin == brain_outcomes.AUTO_ORIGIN
        and best.post_age_hours is not None
        and float(best.post_age_hours) < hours
    )


async def _batch_rows(session: DbSession, account_id: UUID, batch: str) -> list[Any]:
    repo = SourcesRepo(session, account_id)
    return [await repo.require(sid) for sid in await repo.batch_ids(batch)]


async def multi_batch(session: DbSession, account_id: UUID, source_id: UUID) -> str | None:
    """Пачка материала, если в ней больше одного материала; иначе None."""
    src = await SourcesRepo(session, account_id).require(source_id)
    batch = (src.meta or {}).get("batch")
    if not isinstance(batch, str):
        return None
    ids = await SourcesRepo(session, account_id).batch_ids(batch)
    return batch if len(ids) > 1 else None


async def analyzed_ids(session: DbSession, account_id: UUID, batch: str) -> list[UUID]:
    """Разобранные (с текущим разбором) материалы пачки по порядку."""
    analyses = MaterialAnalysesRepo(session, account_id)
    out = []
    for src in await _batch_rows(session, account_id, batch):
        if src.removed_at is None and await analyses.current(src.id) is not None:
            out.append(src.id)
    return out


async def unrated_ids(session: DbSession, account_id: UUID, batch: str) -> list[UUID]:
    """Разобранные материалы пачки без исхода (нет цифр и оценки владельца)."""
    outcomes = MaterialOutcomesRepo(session, account_id)
    return [
        sid
        for sid in await analyzed_ids(session, account_id, batch)
        if bucket(await outcomes.current(sid)) == "unknown"
    ]


async def summarize(session: DbSession, account_id: UUID, batch: str) -> Summary:
    rows = await _batch_rows(session, account_id, batch)
    analyses = MaterialAnalysesRepo(session, account_id)
    outcomes = MaterialOutcomesRepo(session, account_id)
    buckets: Counter[str] = Counter()
    hooks: Counter[str] = Counter()
    devices: Counter[str] = Counter()
    pending = 0
    defer_hours = brain_outcomes.OutcomeConfig.load().defer_hours
    for src in rows:
        if not _settled(src):
            pending += 1
            continue
        if src.removed_at is not None:
            continue
        analysis = await analyses.current(src.id)
        if analysis is None:
            continue
        kind = bucket(await outcomes.current(src.id))
        if kind == "unknown" and await _fresh(session, account_id, src.id, defer_hours):
            kind = "fresh"
        buckets[kind] += 1
        if analysis.hook_type:
            hooks[analysis.hook_type] += 1
        devices.update(analysis.devices or [])

    async def titled(facet: str, counts: Counter[str]) -> list[tuple[str, int]]:
        names = {t.key: t.title for t in await taxonomy.active_terms(session, account_id, facet)}
        return [(names.get(k, k), n) for k, n in counts.most_common(TOP)]

    return Summary(
        batch=batch,
        analyzed=sum(buckets.values()),
        hit=buckets["hit"],
        normal=buckets["normal"],
        flop=buckets["flop"],
        unknown=buckets["unknown"],
        pending=pending,
        fresh=buckets["fresh"],
        hooks=await titled("hook_type", hooks),
        devices=await titled("device", devices),
    )


def render_summary(s: Summary) -> tuple[str, InlineKeyboardMarkup]:
    text = texts.batch_summary(
        s.analyzed, s.hit, s.normal, s.flop, s.unknown, s.hooks, s.devices, s.pending, s.fresh
    )
    # свежие тоже без исхода: оценить их вручную можно сразу
    return text, keyboards.batch_summary(s.batch, s.unknown + s.fresh)


async def send_summary(
    uow_factory: Callable[[], UnitOfWork], notifier: Notifier, account_id: UUID, batch: str
) -> bool:
    """Сводка пачки (в том числе по таймауту); уже отправлена или нечего сводить — False."""
    async with uow_factory() as uow:
        rows = await SourcesRepo(uow.session, account_id).lock_batch(batch)
        if not rows or any((r.meta or {}).get(SENT_KEY) for r in rows):
            return False
        await SourcesRepo(uow.session, account_id).merge_meta(rows[0].id, **{SENT_KEY: True})
        summary = await summarize(uow.session, account_id, batch)
    if summary.analyzed == 0:
        return False  # всё упало — владелец уже получил сообщения об ошибках
    text, keyboard = render_summary(summary)
    await notifier.notify(account_id, text, keyboard)
    return True


async def after_analysis(
    uow_factory: Callable[[], UnitOfWork],
    notifier: Notifier,
    account_id: UUID,
    source_id: UUID,
    *,
    now: datetime,
) -> bool:
    """Материал разобран. True — он из пачки и карточку не слать (сводка будет или ушла)."""
    async with uow_factory() as uow:
        session = uow.session
        batch = await multi_batch(session, account_id, source_id)
        if batch is None:
            return False
        rows = await _batch_rows(session, account_id, batch)
        if any((r.meta or {}).get(SENT_KEY) for r in rows):
            return False  # сводка ушла по таймауту — опоздавший приходит карточкой
        done = all(_settled(r) for r in rows)
        if not done:
            await JobsRepo(session).enqueue(
                account_id=account_id,
                kind=SUMMARY_KIND,
                payload={"batch": batch},
                run_at=now + SUMMARY_TIMEOUT,
                max_attempts=3,
                idempotency_key=f"{SUMMARY_KIND}:{batch}",
            )
    if done:
        await send_summary(uow_factory, notifier, account_id, batch)
    return True


async def set_stance(
    session: DbSession, account_id: UUID, batch: str, stance: str, *, now: datetime
) -> int:
    """«Всё моё» / «Всё вдохновение»: отношение всей пачки + пересмотр доказательств.

    Отношение меняет вес доказательств (свой пост — own_outcome), поэтому по каждому
    разобранному материалу ставится brain.detect с revise. Вернуть число изменённых.
    """
    if stance not in BATCH_STANCES:
        raise ValueError(f"неизвестное отношение: {stance!r}")
    changed = await SourcesRepo(session, account_id).set_stance_for_batch(batch, stance)
    jobs = JobsRepo(session)
    for sid in await analyzed_ids(session, account_id, batch):
        await jobs.enqueue(
            account_id=account_id,
            kind=DETECT_KIND,
            payload={"source_id": str(sid), "revise": True},
            run_at=now,
            max_attempts=3,
            idempotency_key=f"{DETECT_KIND}:stance:{sid}:{stance}:{now.isoformat()}",
        )
    return changed


async def set_stance_for_sources(
    session: DbSession, account_id: UUID, source_ids: list[UUID], stance: str, *, now: datetime
) -> int:
    """Сменить отношение у выбранных материалов + пересмотр доказательств (как `set_stance`).

    У разобранных ставится brain.detect с revise: прежние доказательства отзываются, веса
    знаний пересчитываются, новые пишутся уже с текущим отношением. Вернуть число задач.
    """
    if stance not in BATCH_STANCES:
        raise ValueError(f"неизвестное отношение: {stance!r}")
    repo, jobs, queued = SourcesRepo(session, account_id), JobsRepo(session), 0
    for sid in source_ids:
        src = await repo.update(sid, stance=stance)
        if src.analysis_status != "analyzed":
            continue
        await jobs.enqueue(
            account_id=account_id,
            kind=DETECT_KIND,
            payload={"source_id": str(sid), "revise": True},
            run_at=now,
            max_attempts=3,
            idempotency_key=f"{DETECT_KIND}:stance:{sid}:{stance}:{now.isoformat()}",
        )
        queued += 1
    return queued


async def handle_batch_summary(
    job: Any, *, uow_factory: Callable[[], UnitOfWork], notifier: Notifier | None
) -> bool:
    """Задача brain.batch_summary (таймаут): сводка по тому, что успело разобраться."""
    if notifier is None:
        return False
    return await send_summary(uow_factory, notifier, job.account_id, str(job.payload["batch"]))
