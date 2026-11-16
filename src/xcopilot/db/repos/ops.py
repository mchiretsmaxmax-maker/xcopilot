"""Эксплуатация (этап 15): отметки `ops_marks` и запросы для алертов и /health.

Репозиторий глобальный: часть отметок (бэкап) живёт у системного аккаунта global, часть
проверок (партиции DEFAULT, очередь) — общая для процесса.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Account, OpsMark
from xcopilot.db.partitions import PARTITIONED_TABLES, default_partition_rowcount

BACKUP_OK = "backup.ok"
BACKUP_FAILED = "backup.failed"


@dataclass(frozen=True, slots=True)
class DeadJob:
    id: UUID
    kind: str
    attempts: int
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class QueueStats:
    queued: int
    running: int
    dead_24h: int
    oldest_queued: datetime | None


class OpsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── отметки ─────────────────────────────────────────────────────────────────────────

    async def global_account_id(self) -> UUID:
        stmt = select(Account.id).where(Account.kind == "global")
        return (await self.session.execute(stmt)).scalar_one()

    async def mark_at(self, account_id: UUID, key: str) -> datetime | None:
        stmt = select(OpsMark.at).where(OpsMark.account_id == account_id, OpsMark.key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_mark(
        self, account_id: UUID, key: str, at: datetime, payload: dict[str, Any] | None = None
    ) -> None:
        stmt = insert(OpsMark).values(account_id=account_id, key=key, at=at, payload=payload or {})
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_id", "key"],
            set_={"at": stmt.excluded.at, "payload": stmt.excluded.payload},
        )
        await self.session.execute(stmt)

    async def backup_marks(self) -> tuple[datetime | None, datetime | None]:
        """Время последнего успешного и последнего неудачного бэкапа."""
        glob = await self.global_account_id()
        return await self.mark_at(glob, BACKUP_OK), await self.mark_at(glob, BACKUP_FAILED)

    # ── проверки ────────────────────────────────────────────────────────────────────────

    async def dead_jobs(self, account_id: UUID, since: datetime) -> list[DeadJob]:
        rows = await self._all(
            "SELECT id, kind, attempts, finished_at FROM jobs WHERE account_id = :a"
            " AND status = 'dead' AND coalesce(finished_at, updated_at) >= :s"
            " ORDER BY finished_at NULLS LAST, id",
            a=account_id,
            s=since,
        )
        return [DeadJob(r["id"], r["kind"], r["attempts"], r["finished_at"]) for r in rows]

    async def default_partition_rows(self) -> dict[str, int]:
        """Таблица → строк в партиции DEFAULT (только ненулевые)."""
        counts = {t: await default_partition_rowcount(self.session, t) for t in PARTITIONED_TABLES}
        return {t: n for t, n in counts.items() if n > 0}

    async def auto_promotions(self, account_id: UUID, since: datetime) -> tuple[int, int]:
        """Автоматические повышения знаний за окно: (всего, откачено владельцем)."""
        row = (
            await self._all(
                "SELECT count(*) AS n, count(*) FILTER (WHERE reverted_at IS NOT NULL) AS r"
                " FROM knowledge_transitions WHERE account_id = :a AND actor = 'system'"
                " AND to_status IN ('probation', 'active') AND ts >= :s",
                a=account_id,
                s=since,
            )
        )[0]
        return int(row["n"]), int(row["r"])

    async def analysis_queue(self, account_id: UUID, older_than: datetime) -> tuple[int, int]:
        """Материалы, ждущие разбора: (всего, ждут дольше порога)."""
        row = (
            await self._all(
                "SELECT count(*) AS n, count(*) FILTER (WHERE created_at < :t) AS old"
                " FROM sources WHERE account_id = :a AND analysis_status = 'pending'"
                " AND removed_at IS NULL",
                a=account_id,
                t=older_than,
            )
        )[0]
        return int(row["n"]), int(row["old"])

    async def last_knowledge_snapshot(self, account_id: UUID) -> date | None:
        rows = await self._all(
            "SELECT max(snapshot_date) AS d FROM knowledge_snapshots WHERE account_id = :a",
            a=account_id,
        )
        value = rows[0]["d"]
        return value if isinstance(value, date) else None

    async def queue_stats(self, since: datetime) -> QueueStats:
        row = (
            await self._all(
                "SELECT count(*) FILTER (WHERE status = 'queued') AS q,"
                " count(*) FILTER (WHERE status = 'running') AS r,"
                " count(*) FILTER (WHERE status = 'dead' AND finished_at >= :s) AS d,"
                " min(run_at) FILTER (WHERE status = 'queued') AS oldest FROM jobs",
                s=since,
            )
        )[0]
        return QueueStats(int(row["q"]), int(row["r"]), int(row["d"]), row["oldest"])

    async def ping(self) -> None:
        await self.session.execute(text("SELECT 1"))

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        result = await self.session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]
