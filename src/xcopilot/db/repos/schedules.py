"""Расписания периодических задач (таблица schedules) для планировщика xcopilot.jobs.scheduler.

Репозиторий глобальный: планировщик обходит расписания всех аккаунтов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Account, Schedule


@dataclass(frozen=True, slots=True)
class ScheduleRow:
    """Включённое расписание и пояс его аккаунта."""

    id: UUID
    account_id: UUID
    kind: str
    at_local_time: time
    weekday: int | None
    payload: dict[str, Any]
    last_run_at: datetime | None
    tz: str


class SchedulesRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enabled(self) -> list[ScheduleRow]:
        stmt = (
            select(Schedule, Account.tz)
            .join(Account, Account.id == Schedule.account_id)
            .where(Schedule.enabled.is_(True))
            .order_by(Schedule.at_local_time, Schedule.id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            ScheduleRow(
                id=s.id,
                account_id=s.account_id,
                kind=s.kind,
                at_local_time=s.at_local_time,
                weekday=s.weekday,
                payload=dict(s.payload or {}),
                last_run_at=s.last_run_at,
                tz=tz,
            )
            for s, tz in rows
        ]

    async def mark_run(self, schedule_id: UUID, at: datetime) -> None:
        stmt = update(Schedule).where(Schedule.id == schedule_id).values(last_run_at=at)
        await self.session.execute(stmt)

    async def ensure_for_accounts(self, kind: str, at_local_time: time, weekday: int | None) -> int:
        """Завести расписание kind каждому рабочему аккаунту (не global), где его нет."""
        accounts = select(Account.id).where(Account.kind != "global")
        ids = list((await self.session.execute(accounts)).scalars().all())
        if not ids:
            return 0
        stmt = (
            insert(Schedule)
            .values(
                [
                    {
                        "account_id": i,
                        "kind": kind,
                        "at_local_time": at_local_time,
                        "weekday": weekday,
                    }
                    for i in ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["account_id", "kind"])
            .returning(Schedule.id)
        )
        return len((await self.session.execute(stmt)).scalars().all())
