"""Знания «Мозга» (ТЗ v3.1): единицы знания с версиями и областью, доказательства, связи,
переходы, снимки, векторы и выдача (пакеты, применения, сегменты).

Формулы весов — в функции БД `knowledge_recompute_scores` (миграция 0004); здесь только вызов.
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import (
    KnowledgeApplication,
    KnowledgeEmbedding,
    KnowledgeEvidence,
    KnowledgeItem,
    KnowledgeItemScope,
    KnowledgeItemVersion,
    KnowledgeLink,
    KnowledgePack,
    KnowledgeSnapshot,
    KnowledgeTransition,
    Segment,
)
from xcopilot.db.repos.base import TenantRepo
from xcopilot.errors import NotFound

# Поля текста знания, которые версионируются (knowledge_item_versions).
VERSIONED_FIELDS = (
    "statement",
    "guidance",
    "skeleton",
    "detector",
    "kind",
    "layer",
    "element",
    "target_metric",
)
_SNAPSHOT_FIELDS = (
    "status",
    "owner_state",
    "version",
    "layer",
    "eff_alpha",
    "eff_beta",
    "fit_alpha",
    "fit_beta",
    "eff_conf",
    "fit_conf",
    "quality",
    "evidence_n",
    "own_support",
    "own_contra",
)


class _AccountScoped:
    """Репозиторий таблицы без собственного id: ключ — составной, account_id обязателен."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError(f"{type(self).__name__}: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id


class KnowledgeItemsRepo(TenantRepo[KnowledgeItem]):
    """Знания аккаунта: создание с версией 1 и областью, правка новой версией, переходы."""

    model = KnowledgeItem

    async def create(
        self,
        *,
        scopes: dict[str, Sequence[str]] | None = None,
        changed_by: str = "system",
        user_id: UUID | None = None,
        reason: str = "extracted",
        provenance: dict[str, Any] | None = None,
        **fields: Any,
    ) -> KnowledgeItem:
        """Новое знание (status candidate) + версия 1 + строки области действия."""
        item = await self.add(**fields)
        await self._write_version(item, changed_by, user_id, reason, provenance or {})
        if scopes:
            await self.set_scopes(item.id, scopes)
        return item

    async def _write_version(
        self,
        item: KnowledgeItem,
        changed_by: str,
        user_id: UUID | None,
        reason: str,
        provenance: dict[str, Any],
    ) -> None:
        await self.session.execute(
            insert(KnowledgeItemVersion).values(
                item_id=item.id,
                account_id=self.account_id,
                version=item.version,
                changed_by=changed_by,
                user_id=user_id,
                reason=reason,
                provenance=provenance,
                **{f: getattr(item, f) for f in VERSIONED_FIELDS},
            )
        )

    async def edit(
        self,
        item_id: UUID,
        *,
        changed_by: str,
        reason: str,
        user_id: UUID | None = None,
        provenance: dict[str, Any] | None = None,
        **changes: Any,
    ) -> KnowledgeItem:
        """Изменить текст знания: новая версия, прежняя остаётся в истории."""
        unknown = set(changes) - set(VERSIONED_FIELDS)
        if unknown:
            raise ValueError(f"не поля текста знания: {', '.join(sorted(unknown))}")
        current = await self.require(item_id)
        item = await self.update(item_id, version=current.version + 1, **changes)
        await self._write_version(item, changed_by, user_id, reason, provenance or {})
        return item

    async def versions(self, item_id: UUID) -> list[KnowledgeItemVersion]:
        stmt = (
            select(KnowledgeItemVersion)
            .where(
                KnowledgeItemVersion.account_id == self.account_id,
                KnowledgeItemVersion.item_id == item_id,
            )
            .order_by(KnowledgeItemVersion.version)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def scopes(self, item_id: UUID) -> dict[str, list[str]]:
        """Область действия: {измерение: [значения по алфавиту]}."""
        stmt = (
            select(KnowledgeItemScope.facet, KnowledgeItemScope.value)
            .where(
                KnowledgeItemScope.account_id == self.account_id,
                KnowledgeItemScope.item_id == item_id,
            )
            .order_by(KnowledgeItemScope.facet, KnowledgeItemScope.value)
        )
        out: dict[str, list[str]] = {}
        for facet, value in (await self.session.execute(stmt)).all():
            out.setdefault(facet, []).append(value)
        return out

    async def set_scopes(self, item_id: UUID, scopes: dict[str, Sequence[str]]) -> None:
        """Заменить область действия знания целиком."""
        await self.require(item_id)
        await self.session.execute(
            delete(KnowledgeItemScope).where(
                KnowledgeItemScope.account_id == self.account_id,
                KnowledgeItemScope.item_id == item_id,
            )
        )
        rows = [
            {"item_id": item_id, "account_id": self.account_id, "facet": f, "value": v}
            for f, values in scopes.items()
            for v in sorted(set(values))
        ]
        if rows:
            await self.session.execute(insert(KnowledgeItemScope), rows)

    async def by_status(self, status: str) -> builtins.list[KnowledgeItem]:
        stmt = self.scoped().where(KnowledgeItem.status == status).order_by(KnowledgeItem.id)
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def usable(self) -> builtins.list[KnowledgeItem]:
        """Знания, допущенные к генерации (представление v_knowledge_usable), по качеству."""
        rows = await self.session.execute(
            text("SELECT id FROM v_knowledge_usable WHERE account_id = :a"), {"a": self.account_id}
        )
        ids = [r[0] for r in rows.all()]
        if not ids:
            return []
        stmt = (
            self.scoped()
            .where(KnowledgeItem.id.in_(ids))
            .order_by(KnowledgeItem.quality.desc(), KnowledgeItem.id)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def transition(
        self,
        item_id: UUID,
        to_status: str,
        *,
        actor: str,
        rule: str,
        at: datetime,
        user_id: UUID | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        merged_into: UUID | None = None,
    ) -> KnowledgeTransition:
        """Сменить статус знания и записать переход в журнал."""
        item = await self.require(item_id)
        values: dict[str, Any] = {"status": to_status, "updated_at": at}
        if to_status == "probation":
            values["probation_started_at"] = at
        if to_status == "merged":
            values["merged_into"] = merged_into
        elif item.merged_into is not None:
            values["merged_into"] = None
        await self.update(item_id, **values)
        return await KnowledgeTransitionsRepo(self.session, self.account_id).add(
            item_id=item_id,
            ts=at,
            from_status=item.status,
            to_status=to_status,
            actor=actor,
            user_id=user_id,
            rule=rule,
            reason=reason,
            payload=payload or {},
        )

    async def revert_transition(
        self, transition_id: UUID, *, user_id: UUID | None, at: datetime
    ) -> KnowledgeTransition:
        """Откатить переход: знание возвращается в прежний статус, переход помечается."""
        transitions = KnowledgeTransitionsRepo(self.session, self.account_id)
        t = await transitions.require(transition_id)
        if t.reverted_at is not None:
            return t
        if t.from_status is not None:
            values: dict[str, Any] = {"status": t.from_status, "updated_at": at}
            if t.to_status == "merged":
                values["merged_into"] = None
            await self.update(t.item_id, **values)
        return await transitions.update(transition_id, reverted_at=at, reverted_by=user_id)

    async def recompute(self, item_id: UUID | None = None, *, now: datetime | None = None) -> int:
        """Пересчитать веса (одного знания или всех знаний аккаунта); вернуть число строк."""
        params: dict[str, Any] = {"a": self.account_id, "i": item_id}
        sql = "SELECT knowledge_recompute_scores(:a, :i)"
        if now is not None:
            sql = "SELECT knowledge_recompute_scores(:a, :i, :n)"
            params["n"] = now
        result = (await self.session.execute(text(sql), params)).scalar_one()
        self.session.expire_all()
        return int(result)


class KnowledgeTransitionsRepo(TenantRepo[KnowledgeTransition]):
    model = KnowledgeTransition


class KnowledgeEvidenceRepo(TenantRepo[KnowledgeEvidence]):
    """Доказательства: только добавление; ошибочные отзываются."""

    model = KnowledgeEvidence

    async def revoke(self, evidence_id: UUID, *, at: datetime, reason: str) -> KnowledgeEvidence:
        return await self.update(evidence_id, revoked_at=at, revoked_reason=reason)

    async def for_item(
        self, item_id: UUID, *, include_revoked: bool = False
    ) -> builtins.list[KnowledgeEvidence]:
        stmt = self.scoped().where(KnowledgeEvidence.item_id == item_id)
        if not include_revoked:
            stmt = stmt.where(KnowledgeEvidence.revoked_at.is_(None))
        stmt = stmt.order_by(KnowledgeEvidence.id)
        return builtins.list((await self.session.execute(stmt)).scalars().all())


class KnowledgeLinksRepo(TenantRepo[KnowledgeLink]):
    model = KnowledgeLink

    async def open_conflicts(self) -> builtins.list[KnowledgeLink]:
        stmt = (
            self.scoped()
            .where(KnowledgeLink.status == "open", KnowledgeLink.relation == "contradicts")
            .order_by(KnowledgeLink.id)
        )
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def resolve(self, link_id: UUID, *, resolution: str, at: datetime) -> KnowledgeLink:
        return await self.update(link_id, status="resolved", resolution=resolution, resolved_at=at)


class KnowledgeSnapshotsRepo(_AccountScoped):
    """Ежедневные снимки состояния знаний (партиции по месяцам)."""

    async def take(self, snapshot_date: date) -> int:
        """Снять состояние всех знаний аккаунта на дату (повтор — обновление); вернуть число."""
        cols = ", ".join(_SNAPSHOT_FIELDS)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _SNAPSHOT_FIELDS)
        result = await self.session.execute(
            text(
                f"INSERT INTO knowledge_snapshots (snapshot_date, account_id, item_id, {cols})"
                f" SELECT :d, account_id, id, {cols} FROM knowledge_items WHERE account_id = :a"
                f" ON CONFLICT (snapshot_date, account_id, item_id) DO UPDATE SET {updates}"
            ),
            {"d": snapshot_date, "a": self.account_id},
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    async def state_on(self, day: date) -> list[KnowledgeSnapshot]:
        """Состояние на конец дня (последний снимок не позже day)."""
        latest = (
            select(func.max(KnowledgeSnapshot.snapshot_date))
            .where(
                KnowledgeSnapshot.account_id == self.account_id,
                KnowledgeSnapshot.snapshot_date <= day,
            )
            .scalar_subquery()
        )
        stmt = (
            select(KnowledgeSnapshot)
            .where(
                KnowledgeSnapshot.account_id == self.account_id,
                KnowledgeSnapshot.snapshot_date == latest,
            )
            .order_by(KnowledgeSnapshot.item_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class KnowledgeEmbeddingsRepo(_AccountScoped):
    async def upsert(
        self, item_id: UUID, model_id: UUID, *, version: int, vec: Sequence[float]
    ) -> None:
        """Записать вектор знания для модели (новая версия текста заменяет прежний)."""
        await KnowledgeItemsRepo(self.session, self.account_id).require(item_id)
        stmt = pg_insert(KnowledgeEmbedding).values(
            item_id=item_id,
            account_id=self.account_id,
            model_id=model_id,
            version=version,
            vec=list(vec),
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["item_id", "model_id"],
                set_={"version": stmt.excluded.version, "vec": stmt.excluded.vec},
            )
        )


class KnowledgePacksRepo(TenantRepo[KnowledgePack]):
    model = KnowledgePack


class KnowledgeApplicationsRepo(TenantRepo[KnowledgeApplication]):
    model = KnowledgeApplication

    async def mark_checked(
        self, application_id: UUID, *, applied: bool, score: float, checked_by: str, at: datetime
    ) -> KnowledgeApplication:
        return await self.update(
            application_id,
            applied=applied,
            applied_score=score,
            checked_by=checked_by,
            checked_at=at,
        )


class SegmentsRepo(TenantRepo[Segment]):
    model = Segment

    async def by_key(self, key: str) -> Segment | None:
        stmt = self.scoped().where(Segment.key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def items(self, key: str) -> builtins.list[UUID]:
        """id знаний сегмента (представление v_segment_items) по убыванию качества."""
        if await self.by_key(key) is None:
            raise NotFound(f"segments: {key} не найден")
        rows = await self.session.execute(
            text(
                "SELECT item_id FROM v_segment_items WHERE account_id = :a AND segment_key = :k"
                " ORDER BY quality DESC, item_id"
            ),
            {"a": self.account_id, "k": key},
        )
        return [r[0] for r in rows.all()]
