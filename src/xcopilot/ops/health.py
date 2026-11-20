"""Состояние сервиса для /health (этап 15): БД, провайдеры, очередь, бэкап, «Мозг».

`collect` ничего не бросает: недоступная БД — строка «БД: ❌», остальное «нет данных».
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

import structlog

from xcopilot.clock import Clock
from xcopilot.db.repos import OpsRepo
from xcopilot.db.repos.ops import QueueStats
from xcopilot.db.uow import UnitOfWork
from xcopilot.ops.alerts import BALANCE_MARK
from xcopilot.ops.heartbeat import Heartbeat

log = structlog.get_logger()

OpenSince = Callable[[], Mapping[str, datetime | None]]


@dataclass
class HealthReport:
    now: datetime
    db_ok: bool
    providers: dict[str, datetime | None] = field(default_factory=dict)
    queue: QueueStats | None = None
    backup_ok: datetime | None = None
    backup_failed: datetime | None = None
    analysis_waiting: int | None = None
    analysis_stale: int | None = None
    last_snapshot: date | None = None
    heartbeat_enabled: bool = False
    heartbeat_last: datetime | None = None
    balance_usd: float | None = None  # последний замер баланса DeepSeek (ops.alerts)
    balance_at: datetime | None = None


async def collect(
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    account_id: UUID | None,
    open_since: OpenSince | None = None,
    heartbeat: Heartbeat | None = None,
) -> HealthReport:
    now = clock.now()
    report = HealthReport(now=now, db_ok=False)
    if heartbeat is not None:
        report.heartbeat_enabled = heartbeat.enabled
        report.heartbeat_last = heartbeat.last_ok_at
    if open_since is not None:
        report.providers = dict(open_since())
    try:
        async with uow_factory() as uow:
            repo = OpsRepo(uow.session)
            await repo.ping()
            report.db_ok = True
            report.queue = await repo.queue_stats(now - timedelta(hours=24), now)
            report.backup_ok, report.backup_failed = await repo.backup_marks()
            glob = await repo.global_account_id()
            payload = await repo.mark_payload(glob, BALANCE_MARK) or {}
            if isinstance(payload.get("usd"), int | float):
                report.balance_usd = float(payload["usd"])
                report.balance_at = await repo.mark_at(glob, BALANCE_MARK)
            if account_id is not None:
                report.analysis_waiting, report.analysis_stale = await repo.analysis_queue(
                    account_id, now - timedelta(hours=1)
                )
                report.last_snapshot = await repo.last_knowledge_snapshot(account_id)
    except Exception:
        log.warning("health_collect_failed", exc_info=True)
    return report


def _ago(now: datetime, then: datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} мин назад"
    if minutes < 48 * 60:
        return f"{minutes // 60} ч назад"
    return f"{minutes // 1440} дн назад"


def render(r: HealthReport) -> str:
    lines = ["🩺 Состояние сервиса", "", f"БД: {'✅' if r.db_ok else '❌ недоступна'}"]
    if r.providers:
        lines.append("Провайдеры:")
        for name, since in sorted(r.providers.items()):
            if since is None:
                lines.append(f"  🟢 {name}")
            else:
                lines.append(f"  🔴 {name} — недоступен с {since:%H:%M} UTC ({_ago(r.now, since)})")
    else:
        lines.append("Провайдеры: нет данных")
    if r.queue is not None:
        q = r.queue
        oldest = f", старейшая с {q.oldest_queued:%d.%m %H:%M}" if q.oldest_queued else ""
        lines.append(
            f"Очередь задач: ждут сейчас {q.queued}, запланировано {q.scheduled},"
            f" в работе {q.running}, умерло за сутки {q.dead_24h}{oldest}"
        )
    else:
        lines.append("Очередь задач: нет данных")
    if r.backup_failed is not None and (r.backup_ok is None or r.backup_failed > r.backup_ok):
        lines.append(f"Бэкап: ❌ упал {_ago(r.now, r.backup_failed)}")
    elif r.backup_ok is not None:
        mark = "✅" if r.now - r.backup_ok <= timedelta(hours=26) else "⚠️"
        lines.append(f"Бэкап: {mark} {r.backup_ok:%d.%m %H:%M} UTC ({_ago(r.now, r.backup_ok)})")
    else:
        lines.append("Бэкап: ⚠️ ещё не было")
    if r.analysis_waiting is not None:
        stale = f", дольше часа — {r.analysis_stale}" if r.analysis_stale else ""
        lines.append(f"Очередь разбора: {r.analysis_waiting}{stale}")
    else:
        lines.append("Очередь разбора: нет данных")
    snapshot = f"{r.last_snapshot:%d.%m.%Y}" if r.last_snapshot else "ещё не было"
    lines.append(f"Снимок базы знаний: {snapshot}")
    if r.balance_usd is not None and r.balance_at is not None:
        lines.append(
            f"Баланс DeepSeek: ${r.balance_usd:.2f} (проверен {_ago(r.now, r.balance_at)})"
        )
    else:
        lines.append("Баланс DeepSeek: нет данных")
    if not r.heartbeat_enabled:
        lines.append("Сторож: выключен")
    elif r.heartbeat_last is None:
        lines.append("Сторож: ⚠️ включён, пульса ещё не было")
    else:
        lines.append(f"Сторож: ✅ включён, последний пульс {_ago(r.now, r.heartbeat_last)}")
    return "\n".join(lines)
