"""Решения владельца о знаниях (ТЗ v3.1, 9C): подтвердить, изменить, закрепить, отключить,
удалить, добавить своё, развести противоречие, убрать доказательство.

Все функции проверяют, что `user_id` — владелец аккаунта (`NotOwner`), пишут событие
`knowledge.owner_decision` (от него считается `brain_confirm_load_week`) и меняют базу тем же
путём, что и автоматика: `lifecycle.apply` с `user_id` даёт actor owner. «Удалить» — терминальный
статус `rejected`: автоматика его не трогает, а похожие кандидаты 30 дней не предлагаются.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Literal
from uuid import UUID

from xcopilot.brain import lifecycle
from xcopilot.brain.context import BrainContext, NoEmbedder
from xcopilot.brain.scoring import recompute
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.brain_owner import OwnerRepo
from xcopilot.db.repos.knowledge import (
    KnowledgeEmbeddingsRepo,
    KnowledgeItemsRepo,
    KnowledgeLinksRepo,
)
from xcopilot.errors import NotFound
from xcopilot.metrics.events import EventType, emit

SUPPRESS_DAYS = 30
Resolution = Literal["first", "second", "scope"]
Decision = Literal[
    "confirm", "reject", "not_mine", "pin", "unpin", "disable", "enable", "edit", "delete"
]
GOALS = frozenset({"reach", "engagement", "replies", "saves"})
_TERMINAL = frozenset({"rejected", "merged"})
_URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|io|org|net|xyz|ru)\b", re.I)
_HANDLE = re.compile(r"(?:^|\s)@\w{2,}")
_TICKER = re.compile(r"(?:^|\s)\$[A-Za-z]{2,}")
_ADDRESS = re.compile(
    r"игнорируй|забудь (?:все |предыдущие )?инструкц|ignore (?:all|previous|the above)|"
    r"system prompt|системн\w+ промпт|ты\s+—?\s*(?:ассистент|ии|модель)|as an ai",
    re.I,
)


class NotOwner(PermissionError):
    """Менять знания может только владелец аккаунта."""


class InvalidItem(ValueError):
    """Текст знания не прошёл проверку схемы или знание в терминальном статусе."""


async def require_owner(ctx: BrainContext, user_id: UUID) -> None:
    if await OwnerRepo(ctx.session, ctx.account_id).role(user_id) != "owner":
        raise NotOwner("менять знания может только владелец")


def check_text(statement: str, guidance: str) -> tuple[str, str]:
    """Проверка текста знания: длины схемы, без ссылок, хэндлов, тикеров и обращений к модели."""
    statement, guidance = statement.strip(), guidance.strip()
    if not 15 <= len(statement) <= 240:
        raise InvalidItem("утверждение — от 15 до 240 знаков")
    if not 10 <= len(guidance) <= 200:
        raise InvalidItem("указание — от 10 до 200 знаков")
    for part in (statement, guidance):
        if _URL.search(part) or _HANDLE.search(part) or _TICKER.search(part):
            raise InvalidItem("знание без ссылок, хэндлов и тикеров")
        if _ADDRESS.search(part):
            raise InvalidItem("знание не может содержать обращений к модели")
    return statement, guidance


async def _decision(ctx: BrainContext, item_id: UUID, user_id: UUID, decision: Decision) -> None:
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.KNOWLEDGE_OWNER_DECISION,
        actor="user",
        user_id=user_id,
        entity=("knowledge_item", item_id),
        props={"item_id": str(item_id), "decision": decision},
        ts=ctx.clock.now(),
    )


async def _embed(ctx: BrainContext, item_id: UUID, text: str, version: int) -> None:
    if isinstance(ctx.embedder, NoEmbedder):
        return
    model = await BrainRepo(ctx.session, ctx.account_id).active_embedding_model()
    if model is None:
        return
    vec = (await asyncio.to_thread(ctx.embedder.embed, [text]))[0]
    await KnowledgeEmbeddingsRepo(ctx.session, ctx.account_id).upsert(
        item_id, model, version=version, vec=vec
    )


async def _fit_evidence(ctx: BrainContext, item_id: UUID) -> None:
    """Подтверждение владельца — доказательство совместимости с большим весом (owner_confirm)."""
    repo = BrainRepo(ctx.session, ctx.account_id)
    if await OwnerRepo(ctx.session, ctx.account_id).has_owner_confirmation(item_id):
        return
    now = ctx.clock.now()
    evidence_id = await repo.insert_evidence(
        item_id=item_id,
        dimension="fit",
        basis="owner_decision",
        polarity=1,
        w_source=ctx.config.weights.review["owner_confirm"],
        w_scope=1.0,
        w_magnitude=1.0,
        observed_at=now,
        created_at=now,
    )
    if evidence_id is not None:
        await emit(
            ctx.session,
            account_id=ctx.account_id,
            type=EventType.KNOWLEDGE_EVIDENCE_ADDED,
            entity=("knowledge_item", item_id),
            props={
                "item_id": str(item_id),
                "evidence_id": str(evidence_id),
                "dimension": "fit",
                "basis": "owner_decision",
                "polarity": 1,
            },
            ts=now,
        )


async def _live_item(ctx: BrainContext, item_id: UUID) -> tuple[str, str]:
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).require(item_id)
    if item.status in _TERMINAL:
        raise InvalidItem("знание удалено или влито в другое")
    return item.status, item.owner_state


async def _activate(ctx: BrainContext, item_id: UUID, user_id: UUID, owner_state: str) -> None:
    status, current = await _live_item(ctx, item_id)
    if status in ("candidate", "probation", "weakened", "archived"):
        await lifecycle.apply(
            ctx,
            lifecycle.Transition(
                item_id, status, "active", "owner_confirm", "владелец взял в работу"
            ),
            user_id=user_id,
        )
    if not (owner_state == "confirmed" and current == "pinned"):
        await BrainRepo(ctx.session, ctx.account_id).set_owner_state(item_id, owner_state)
    await _fit_evidence(ctx, item_id)
    await recompute(ctx, item_id)


async def confirm(ctx: BrainContext, item_id: UUID, user_id: UUID) -> None:
    """«В работу»: знание активно, решение владельца учтено доказательством совместимости."""
    await require_owner(ctx, user_id)
    await _activate(ctx, item_id, user_id, "confirmed")
    await _decision(ctx, item_id, user_id, "confirm")


async def confirm_batch(ctx: BrainContext, item_ids: Sequence[UUID], user_id: UUID) -> int:
    """Пакетное «принять»: только перечисленные; класс A и удалённые пропускаются."""
    await require_owner(ctx, user_id)
    done = 0
    items = KnowledgeItemsRepo(ctx.session, ctx.account_id)
    for item_id in item_ids:
        item = await items.get(item_id)
        if item is None or item.status in _TERMINAL or item.risk_class == "A":
            continue
        await _activate(ctx, item_id, user_id, "confirmed")
        await _decision(ctx, item_id, user_id, "confirm")
        done += 1
    return done


async def reject(ctx: BrainContext, item_id: UUID, user_id: UUID, *, delete: bool = False) -> None:
    """Терминально: знание rejected, похожие кандидаты 30 дней не предлагаются."""
    await require_owner(ctx, user_id)
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).require(item_id)
    if item.status == "rejected":
        return
    if item.status == "merged":
        raise InvalidItem("знание влито в другое")
    await _embed(ctx, item_id, item.statement, item.version)
    await lifecycle.apply(
        ctx,
        lifecycle.Transition(
            item_id, item.status, "rejected", "owner_reject", "владелец удалил знание"
        ),
        user_id=user_id,
    )
    await _decision(ctx, item_id, user_id, "delete" if delete else "reject")


async def is_suppressed(ctx: BrainContext, statement: str) -> bool:
    """Похоже ли утверждение на знание, отклонённое владельцем за последние 30 дней."""
    if isinstance(ctx.embedder, NoEmbedder):
        return False
    repo = OwnerRepo(ctx.session, ctx.account_id)
    threshold = await BrainRepo(ctx.session, ctx.account_id).related_threshold()
    return await repo.rejected_similar(
        (await asyncio.to_thread(ctx.embedder.embed, [statement]))[0],
        since=ctx.clock.now() - timedelta(days=SUPPRESS_DAYS),
        threshold=threshold,
    )


async def edit(
    ctx: BrainContext, item_id: UUID, user_id: UUID, statement: str, guidance: str
) -> int:
    """Новая версия текста: доказательства сохраняются, вектор пересчитывается."""
    await require_owner(ctx, user_id)
    statement, guidance = check_text(statement, guidance)
    await _live_item(ctx, item_id)
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).edit(
        item_id,
        changed_by="owner",
        user_id=user_id,
        reason="owner_edit",
        statement=statement,
        guidance=guidance,
    )
    await _embed(ctx, item_id, statement, item.version)
    await _decision(ctx, item_id, user_id, "edit")
    return int(item.version)


async def pin(ctx: BrainContext, item_id: UUID, user_id: UUID) -> None:
    """Закрепить: знание активно и всегда идёт в пакет."""
    await require_owner(ctx, user_id)
    await _activate(ctx, item_id, user_id, "pinned")
    await _decision(ctx, item_id, user_id, "pin")


async def disable(
    ctx: BrainContext, item_id: UUID, user_id: UUID, *, not_mine: bool = False
) -> None:
    """Не применять: в базе остаётся (как ремесло), в черновики не идёт."""
    await require_owner(ctx, user_id)
    await _live_item(ctx, item_id)
    await BrainRepo(ctx.session, ctx.account_id).set_owner_state(item_id, "disabled")
    await _decision(ctx, item_id, user_id, "not_mine" if not_mine else "disable")


async def remove_evidence(ctx: BrainContext, evidence_id: UUID, user_id: UUID) -> None:
    """Убрать одно доказательство; веса знания пересчитываются, материал остаётся."""
    await require_owner(ctx, user_id)
    row = await OwnerRepo(ctx.session, ctx.account_id).evidence_row(evidence_id)
    if row is None:
        raise NotFound(f"доказательство {evidence_id} не найдено")
    repo = BrainRepo(ctx.session, ctx.account_id)
    await repo.revoke_evidence(at=ctx.clock.now(), reason="owner_removed", ids=[evidence_id])
    await recompute(ctx, row["item_id"])
    await _decision(ctx, row["item_id"], user_id, "edit")


async def add_manual(
    ctx: BrainContext, user_id: UUID, text: str, *, formats: Sequence[str] | None = None
) -> UUID:
    """Своё знание: проходит те же проверки схемы, сразу «в работе», источник — владелец.

    formats — область по формату (например ["reply"]); None — работает везде.
    """
    await require_owner(ctx, user_id)
    statement, guidance = check_text(text, text[:200])
    now = ctx.clock.now()
    item = await KnowledgeItemsRepo(ctx.session, ctx.account_id).create(
        kind="thesis",
        layer="craft",
        element="general",
        statement=statement,
        guidance=guidance,
        target_metric="quality",
        detector={"type": "none"},
        risk_class="B",
        status="active",
        owner_state="confirmed",
        origin="owner",
        created_at=now,
        updated_at=now,
        changed_by="owner",
        user_id=user_id,
        reason="owner_added",
        provenance={"origin": "owner"},
    )
    if formats:
        await KnowledgeItemsRepo(ctx.session, ctx.account_id).set_scopes(
            item.id, {"format": list(formats)}
        )
    await _embed(ctx, item.id, statement, item.version)
    await _fit_evidence(ctx, item.id)
    await recompute(ctx, item.id)
    await _decision(ctx, item.id, user_id, "confirm")
    return item.id


async def set_formats(
    ctx: BrainContext, item_id: UUID, user_id: UUID, formats: Sequence[str] | None
) -> None:
    """«Где работает»: заменить область по формату (None — везде), прочие измерения не трогать."""
    await require_owner(ctx, user_id)
    items = KnowledgeItemsRepo(ctx.session, ctx.account_id)
    scopes: dict[str, Sequence[str]] = {
        f: v for f, v in (await items.scopes(item_id)).items() if f != "format"
    }
    if formats:
        scopes["format"] = list(formats)
    await items.set_scopes(item_id, scopes)


async def _keep(ctx: BrainContext, item_id: UUID, user_id: UUID) -> None:
    opened = await BrainRepo(ctx.session, ctx.account_id).conflict_transition(item_id)
    if opened is not None:
        await lifecycle.revert(ctx, opened["id"], user_id)


async def resolve_conflict(
    ctx: BrainContext, link_id: UUID, user_id: UUID, resolution: Resolution
) -> None:
    """Первое, второе или «оба»: развести по целям (у каждого своя цель в области действия)."""
    await require_owner(ctx, user_id)
    link = await OwnerRepo(ctx.session, ctx.account_id).link(link_id)
    if link is None or link["relation"] != "contradicts":
        raise NotFound(f"противоречие {link_id} не найдено")
    if link["status"] != "open":
        raise InvalidItem("противоречие уже решено")
    first, second = link["from_item_id"], link["to_item_id"]
    items = KnowledgeItemsRepo(ctx.session, ctx.account_id)
    links = KnowledgeLinksRepo(ctx.session, ctx.account_id)
    now = ctx.clock.now()
    if resolution == "scope":
        a, b = await items.require(first), await items.require(second)
        if a.target_metric == b.target_metric or not {a.target_metric, b.target_metric} <= GOALS:
            raise InvalidItem("развести нечем: у знаний одна цель")
        for item in (a, b):
            scopes = await items.scopes(item.id)
            scopes["goal"] = [item.target_metric]
            await items.set_scopes(item.id, scopes)
            await _keep(ctx, item.id, user_id)
            await _decision(ctx, item.id, user_id, "confirm")
        await links.resolve(link_id, resolution="scoped", at=now)
        return
    winner, loser = (first, second) if resolution == "first" else (second, first)
    await links.resolve(link_id, resolution=resolution, at=now)
    await _keep(ctx, winner, user_id)
    status = (await items.require(loser)).status
    if status not in ("archived", "rejected", "merged"):
        await lifecycle.apply(
            ctx,
            lifecycle.Transition(
                loser, status, "archived", "owner_conflict", "владелец выбрал другое знание"
            ),
            user_id=user_id,
        )
    await _decision(ctx, winner, user_id, "confirm")


async def autonomy(ctx: BrainContext) -> Literal["auto", "manual"]:
    """`manual` — автоиспытания выключены: новые знания ждут решения владельца."""
    value = await OwnerRepo(ctx.session, ctx.account_id).autonomy()
    return "manual" if value == "manual" else "auto"


async def set_autonomy(ctx: BrainContext, user_id: UUID, value: Literal["auto", "manual"]) -> None:
    await require_owner(ctx, user_id)
    await OwnerRepo(ctx.session, ctx.account_id).set_autonomy(value)
