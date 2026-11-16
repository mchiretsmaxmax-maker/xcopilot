"""Запросы своей аналитики (этап 14): посты со снимками, отчёты дня, метрики для /stats.

Прикладной код (`xcopilot.analytics`, бот) ходит в БД только отсюда.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LATEST_IMPRESSIONS = """
SELECT s.id AS source_id, s.external_id, s.kind, s.posted_at, m.impressions
FROM sources s
JOIN LATERAL (
    SELECT p.impressions FROM post_metric_snapshots p
    WHERE p.account_id = s.account_id AND p.x_post_id = s.external_id
      AND p.impressions IS NOT NULL
    ORDER BY p.ts DESC LIMIT 1
) m ON true
WHERE s.account_id = :a AND s.stance = 'own' AND s.removed_at IS NULL
  AND s.external_id IS NOT NULL AND s.posted_at >= :since AND s.posted_at <= :until
"""


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result.all()]


class AnalyticsRepo:
    """Запросы одного аккаунта; account_id обязателен."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("AnalyticsRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        return _rows(await self.session.execute(text(sql), {"a": self.account_id, **params}))

    async def _scalar(self, sql: str, **params: Any) -> Any:
        return (await self.session.execute(text(sql), {"a": self.account_id, **params})).scalar()

    # --- синк и снимки ------------------------------------------------------------------

    async def max_own_post_id(self) -> str | None:
        """Наибольший id своего поста среди материалов (для since_id)."""
        value = await self._scalar(
            "SELECT max(external_id::numeric) FROM sources WHERE account_id = :a"
            " AND stance = 'own' AND external_id ~ '^[0-9]+$'"
        )
        return None if value is None else str(int(value))

    async def insert_snapshot(
        self,
        *,
        x_post_id: str,
        ts: datetime,
        age_minutes: int,
        impressions: int | None,
        likes: int,
        replies: int,
        reposts: int,
        quotes: int,
        bookmarks: int,
        url_clicks: int | None,
        profile_clicks: int | None,
    ) -> None:
        await self.session.execute(
            text(
                "INSERT INTO post_metric_snapshots (id, account_id, x_post_id, ts, age_minutes,"
                " impressions, likes, replies, reposts, quotes, bookmarks, url_clicks,"
                " profile_clicks, raw) VALUES (:i, :a, :x, :ts, :age, :imp, :lk, :rp, :rt, :q,"
                " :b, :uc, :pc, CAST(:raw AS jsonb))"
            ),
            {
                "i": uuid4(),
                "a": self.account_id,
                "x": x_post_id,
                "ts": ts,
                "age": age_minutes,
                "imp": impressions,
                "lk": likes,
                "rp": replies,
                "rt": reposts,
                "q": quotes,
                "b": bookmarks,
                "uc": url_clicks,
                "pc": profile_clicks,
                "raw": json.dumps({"x_post_id": x_post_id}),
            },
        )

    async def flag_bot_assisted(self) -> None:
        """Своим постам без пометки: «с ботом» — пост связан с публикацией, иначе «без»."""
        await self.session.execute(
            text(
                "UPDATE sources s SET meta = s.meta || jsonb_build_object("
                "'bot_assisted', EXISTS (SELECT 1 FROM publications p"
                " WHERE p.account_id = s.account_id AND p.x_post_id = s.external_id))"
                " WHERE s.account_id = :a AND s.stance = 'own' AND s.external_id IS NOT NULL"
                " AND (NOT s.meta ? 'bot_assisted' OR (s.meta ->> 'bot_assisted' = 'false'"
                " AND EXISTS (SELECT 1 FROM publications p WHERE p.account_id = s.account_id"
                " AND p.x_post_id = s.external_id)))"
            ),
            {"a": self.account_id},
        )

    # --- базовый уровень и закономерности -------------------------------------------------

    async def own_posts_with_views(
        self, since: datetime, until: datetime, fmt: str | None = None
    ) -> list[dict[str, Any]]:
        """Свои посты периода с показами по последнему снимку, по возрастанию времени."""
        sql = _LATEST_IMPRESSIONS + (" AND s.kind = :k" if fmt is not None else "")
        params: dict[str, Any] = {"since": since, "until": until}
        if fmt is not None:
            params["k"] = fmt
        return await self._all(sql + " ORDER BY s.posted_at", **params)

    async def document_lengths(self, source_ids: Sequence[UUID]) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT d.source_id, length(coalesce(d.text_clean, d.text)) AS n FROM documents d"
            " WHERE d.account_id = :a AND d.source_id = ANY(:s)",
            s=list(source_ids),
        )

    async def topic_tags(self, source_ids: Sequence[UUID]) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT d.source_id, t AS tag FROM documents d JOIN chunks c ON c.document_id = d.id"
            " CROSS JOIN LATERAL unnest(c.tags) AS t"
            " WHERE d.account_id = :a AND d.source_id = ANY(:s) AND t LIKE 'topic:%'",
            s=list(source_ids),
        )

    # --- отчёт дня ------------------------------------------------------------------------

    async def publications_count(self, start: datetime, end: datetime) -> int:
        return int(
            await self._scalar(
                "SELECT count(*) FROM publications WHERE account_id = :a"
                " AND published_at >= :s AND published_at < :e",
                s=start,
                e=end,
            )
        )

    async def own_posts_count(self, start: datetime, end: datetime) -> int:
        return int(
            await self._scalar(
                "SELECT count(*) FROM sources WHERE account_id = :a AND stance = 'own'"
                " AND external_id IS NOT NULL AND removed_at IS NULL"
                " AND posted_at >= :s AND posted_at < :e",
                s=start,
                e=end,
            )
        )

    async def review_counts(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(решений владельца, одобрений без правок) за период."""
        row = (
            await self.session.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE action = 'approve'"
                    " AND coalesce(edit_distance, 0) = 0) FROM reviews WHERE account_id = :a"
                    " AND created_at >= :s AND created_at < :e"
                    " AND action IN ('approve', 'edit', 'reject')"
                ),
                {"a": self.account_id, "s": start, "e": end},
            )
        ).one()
        return int(row[0]), int(row[1])

    async def spend_micro(self, start: datetime, end: datetime) -> int:
        return int(
            await self._scalar(
                "SELECT (SELECT coalesce(sum(cost_micro_usd), 0) FROM llm_calls"
                " WHERE account_id = :a AND ts >= :s AND ts < :e)"
                " + (SELECT coalesce(sum(cost_micro_usd), 0) FROM x_api_calls"
                " WHERE account_id = :a AND ts >= :s AND ts < :e)",
                s=start,
                e=end,
            )
        )

    async def own_outcome_counts(self, start: datetime, end: datetime) -> tuple[int, int]:
        """(подтверждённых, опровергнутых знаний) доказательствами own_outcome за период."""
        row = (
            await self.session.execute(
                text(
                    "SELECT count(DISTINCT item_id) FILTER (WHERE polarity = 1),"
                    " count(DISTINCT item_id) FILTER (WHERE polarity = -1) FROM knowledge_evidence"
                    " WHERE account_id = :a AND basis = 'own_outcome' AND revoked_at IS NULL"
                    " AND created_at >= :s AND created_at < :e"
                ),
                {"a": self.account_id, "s": start, "e": end},
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)

    async def first_refuted_statement(self, start: datetime, end: datetime) -> str | None:
        value = await self._scalar(
            "SELECT k.statement FROM knowledge_evidence e JOIN knowledge_items k"
            " ON k.id = e.item_id WHERE e.account_id = :a AND e.basis = 'own_outcome'"
            " AND e.polarity = -1 AND e.revoked_at IS NULL AND e.created_at >= :s"
            " AND e.created_at < :e ORDER BY e.created_at, e.id LIMIT 1",
            s=start,
            e=end,
        )
        return None if value is None else str(value)

    # --- метрики -------------------------------------------------------------------------

    async def weekly_values(self, metric_key: str) -> list[float]:
        """Недельные значения метрики по возрастанию недели."""
        rows = await self._all(
            "SELECT value FROM metric_values WHERE account_id = :a AND metric_key = :k"
            " AND grain = 'week' AND value IS NOT NULL ORDER BY period_start",
            k=metric_key,
        )
        return [float(r["value"]) for r in rows]

    async def daily_values(self, since: date, until: date) -> list[dict[str, Any]]:
        """Суточные метрики без измерений за окно дат (для /stats)."""
        return await self._all(
            "SELECT metric_key, value, sample_size FROM metric_values"
            " WHERE account_id = :a AND grain = 'day' AND dims = '{}'::jsonb"
            " AND period_start >= :f AND period_start <= :t AND value IS NOT NULL",
            f=since,
            t=until,
        )
