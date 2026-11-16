"""Алерты владельцу в Telegram (этап 15, задачи 6 и 13; ADR 0028).

`check_alerts(deps, account_id)` — чистая проверка: что сейчас сломано у аккаунта и в процессе.
`send_alerts` — то же плюс отправка владельцу, но один и тот же алерт (ключ) не чаще, чем
`Alert.repeat` (по умолчанию час); время последней отправки — в `ops_marks` (`alert:<ключ>`).
Отметка ставится только после доставки хотя бы одному владельцу — недошедший алерт повторится.
Задача `ops.alerts` ставится планировщиком каждые 15 минут на аккаунт.

Проверки:
- `job_dead` — задача аккаунта умерла за последние сутки (по одному алерту на задачу, без
  повторов): «⚠️ Задача writer.generate не выполнена после 5 попыток (код E-4f2a)»;
- `budget_80` — потрачено ≥ 80% дневного или месячного лимита (модели или X);
- `circuit_open` — предохранитель провайдера разомкнут дольше 15 минут;
- `default_partition` — строки в партиции DEFAULT журналов (не хватает месячных партиций);
- `backup_stale` — успешного бэкапа нет 26 часов или последний бэкап упал;
- `voice_drift` — дрейф голоса (правило этапа 14, `analytics.drift`);
- `auto_revert_high` — владелец откатил больше 30% автоматических повышений знаний за неделю;
- `analysis_queue_stale` — материалы ждут разбора дольше часа.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

import structlog

from xcopilot.analytics.drift import drift_alert
from xcopilot.clock import Clock
from xcopilot.db.repos import OpsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job
from xcopilot.llm.budget import BudgetGuard

log = structlog.get_logger()

ALERTS_KIND = "ops.alerts"
ALERTS_EVERY = timedelta(minutes=15)
DEAD_WINDOW = timedelta(hours=24)
CIRCUIT_AFTER = timedelta(minutes=15)
BACKUP_MAX_AGE = timedelta(hours=26)
REVERT_WINDOW = timedelta(days=7)
REVERT_RATE = 0.3
REVERT_MIN_SAMPLE = 3
ANALYSIS_MAX_WAIT = timedelta(hours=1)
HOUR = timedelta(hours=1)
BUDGET_SCOPES = {"llm": "на модели", "x_api": "на X API"}
PERIODS = {"day": "сегодня", "month": "в этом месяце"}


@dataclass(frozen=True, slots=True)
class Alert:
    """Код проверки, текст владельцу, ключ для «не повторять» и пауза между повторами.

    `repeat=None` — один раз (повторять бессмысленно, например конкретная умершая задача).
    """

    code: str
    text: str
    key: str
    repeat: timedelta | None = HOUR


class OwnerNotifier(Protocol):
    async def notify(self, account_id: UUID, text: str) -> int:
        """Отправить владельцам; вернуть число доставленных (0 — не дошло никому)."""
        ...


@dataclass
class AlertDeps:
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    budget: BudgetGuard | None = None
    # провайдер → с какого момента разомкнут предохранитель (None — замкнут); шлюз процесса
    open_since: Callable[[], Mapping[str, datetime | None]] | None = None
    env: str = "prod"  # на dev бэкапа нет и не будет — backup_stale молчит


def job_code(job_id: UUID) -> str:
    """Короткий код задачи для владельца и поиска по логу (конец UUID: начало — время)."""
    return f"E-{job_id.hex[-4:]}"


async def check_alerts(deps: AlertDeps, account_id: UUID) -> list[Alert]:
    """Все сработавшие проверки аккаунта (и общие для процесса) на текущий момент."""
    now = deps.clock.now()
    alerts: list[Alert] = []
    async with deps.uow_factory() as uow:
        repo = OpsRepo(uow.session)
        for job in await repo.dead_jobs(account_id, now - DEAD_WINDOW):
            alerts.append(
                Alert(
                    "job_dead",
                    f"⚠️ Задача {job.kind} не выполнена после {job.attempts} попыток"
                    f" (код {job_code(job.id)})",
                    f"job_dead:{job.id}",
                    repeat=None,
                )
            )
        if rows := await repo.default_partition_rows():
            tables = ", ".join(f"{t} ({n})" for t, n in sorted(rows.items()))
            alerts.append(
                Alert(
                    "default_partition",
                    f"⚠️ Строки попали в партицию DEFAULT: {tables}. Не хватает месячных партиций —"
                    " см. runbook, раздел «Партиции».",
                    "default_partition",
                )
            )
        ok, failed = await repo.backup_marks()
        if deps.env != "dev" and (backup := _backup_alert(now, ok, failed)):
            alerts.append(backup)
        drift = await drift_alert(uow.session, account_id)
        if drift is not None:
            alerts.append(Alert("voice_drift", f"⚠️ {drift}", "voice_drift", repeat=HOUR * 24))
        total, reverted = await repo.auto_promotions(account_id, now - REVERT_WINDOW)
        if total >= REVERT_MIN_SAMPLE and reverted / total > REVERT_RATE:
            alerts.append(
                Alert(
                    "auto_revert_high",
                    f"⚠️ За неделю ты откатил {round(reverted / total * 100)}% автоматических"
                    f" решений базы знаний ({reverted} из {total}). Похоже, пороги слишком мягкие:"
                    " проверь /brain.",
                    "auto_revert_high",
                    repeat=HOUR * 24,
                )
            )
        waiting, old = await repo.analysis_queue(account_id, now - ANALYSIS_MAX_WAIT)
        if old:
            alerts.append(
                Alert(
                    "analysis_queue_stale",
                    f"⚠️ Очередь разбора стоит: {old} из {waiting} материалов ждут дольше часа."
                    " Проверь /health и модель-разборщика.",
                    "analysis_queue_stale",
                )
            )
    alerts += await _budget_alerts(deps, account_id)
    alerts += _circuit_alerts(deps, now)
    return alerts


def _backup_alert(now: datetime, ok: datetime | None, failed: datetime | None) -> Alert | None:
    if failed is not None and (ok is None or failed > ok):
        text = f"⚠️ Бэкап не прошёл ({failed:%d.%m %H:%M} UTC). См. runbook, раздел «Бэкап»."
    elif ok is None:
        text = "⚠️ Бэкапа базы ещё не было. Настрой cron по runbook, раздел «Бэкап»."
    elif now - ok > BACKUP_MAX_AGE:
        hours = int((now - ok).total_seconds() // 3600)
        text = f"⚠️ Бэкап не проходил {hours} ч (последний {ok:%d.%m %H:%M} UTC)."
    else:
        return None
    return Alert("backup_stale", text, "backup_stale")


async def _budget_alerts(deps: AlertDeps, account_id: UUID) -> list[Alert]:
    if deps.budget is None:
        return []
    alerts: list[Alert] = []
    for scope, label in BUDGET_SCOPES.items():
        for lim, spent in await deps.budget.usage(account_id, scope):
            if lim.limit <= 0 or spent * 100 < lim.limit * deps.budget.warn_percent:
                continue
            percent = spent * 100 // lim.limit
            alerts.append(
                Alert(
                    "budget_80",
                    f"⚠️ Расход {label} {PERIODS.get(lim.period, lim.period)}: {percent}% лимита"
                    f" (${spent / 1e6:.2f} из ${lim.limit / 1e6:.2f}).",
                    f"budget_80:{scope}:{lim.period}",
                    repeat=HOUR * 12,
                )
            )
    return alerts


def _circuit_alerts(deps: AlertDeps, now: datetime) -> list[Alert]:
    if deps.open_since is None:
        return []
    stuck = sorted(
        (name, since)
        for name, since in deps.open_since().items()
        if since is not None and now - since >= CIRCUIT_AFTER
    )
    if not stuck:
        return []
    names = ", ".join(f"{name} (с {since:%H:%M} UTC)" for name, since in stuck)
    return [
        Alert(
            "circuit_open",
            f"⚠️ Провайдер недоступен дольше 15 минут: {names}. Задачи ждут и повторятся сами.",
            "circuit_open",
        )
    ]


async def send_alerts(deps: AlertDeps, account_id: UUID, notifier: OwnerNotifier) -> list[Alert]:
    """Отправить владельцу сработавшие алерты, кроме недавно отправленных; вернуть отправленные."""
    now = deps.clock.now()
    sent: list[Alert] = []
    for alert in await check_alerts(deps, account_id):
        key = f"alert:{alert.key}"
        async with deps.uow_factory() as uow:
            last = await OpsRepo(uow.session).mark_at(account_id, key)
        if last is not None and (alert.repeat is None or now - last < alert.repeat):
            continue
        if await notifier.notify(account_id, alert.text) <= 0:
            log.warning("ops_alert_not_delivered", code=alert.code, key=alert.key)
            continue  # без отметки: следующий проход (через 15 минут) отправит снова
        async with deps.uow_factory() as uow:
            await OpsRepo(uow.session).set_mark(account_id, key, now, {"text": alert.text})
        log.warning("ops_alert_sent", code=alert.code, key=alert.key)
        sent.append(alert)
    return sent


async def handle_ops_alerts(job: Job, *, deps: AlertDeps, notifier: OwnerNotifier) -> int:
    """Задача `ops.alerts`: проверить и отправить; вернуть число отправленных."""
    return len(await send_alerts(deps, job.account_id, notifier))
