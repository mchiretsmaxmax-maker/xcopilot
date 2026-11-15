"""Стартовое наполнение (ТЗ v3.1, 9A, задача 9): анти-AI правила как знания класса C.

Правила этапа 8 (`style/seed/anti_ai_{ru,en}.yaml`) загружаются в системный аккаунт global один
раз (ключ правила — в provenance версии), как знания слоя humanness класса C: сразу действуют как
предупреждение линтера и пункт «чего избегать». При онбординге аккаунт получает копии с
`parent_item_id` и априором, ослабленным вдвое (раздел 8.3).
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo
from xcopilot.db.uow import DbSession
from xcopilot.style.global_rules import SeedRule, read_seed

_COPY_FIELDS = (
    "kind",
    "layer",
    "element",
    "statement",
    "guidance",
    "skeleton",
    "target_metric",
    "detector",
    "risk_class",
    "status",
)


def seed_key(rule: SeedRule) -> str:
    digest = hashlib.sha256(rule.text.encode()).hexdigest()[:16]
    return f"anti_ai:{rule.lang}:{digest}"


async def load_global_seed(session: DbSession) -> int:
    """Загрузить анти-AI правила в системный аккаунт один раз; вернуть число новых знаний."""
    glob = await AccountsRepo(session).get_global()
    known = await BrainRepo(session, glob.id).seed_keys()
    items = KnowledgeItemsRepo(session, glob.id)
    created = 0
    for rule in read_seed():
        key = seed_key(rule)
        if key in known:
            continue
        await items.create(
            kind="anti_pattern",
            layer="humanness",
            element="general",
            statement=rule.text[:240],
            guidance=rule.text[:200],
            target_metric="quality",
            detector={"type": "none"},
            risk_class="C",
            status="active",
            origin="seed",
            changed_by="system",
            reason="extracted",
            scopes={"lang": [rule.lang]},
            provenance={"seed_key": key, "seed_version": rule.version, "seed_kind": rule.kind},
        )
        known.add(key)
        created += 1
    return created


async def copy_seed_to_account(ctx: BrainContext) -> list[UUID]:
    """Копии общих знаний сида в аккаунт: parent_item_id и априор, ослабленный вдвое."""
    glob = await AccountsRepo(ctx.session).get_global()
    parents = await BrainRepo(ctx.session, glob.id).seed_items()
    repo = BrainRepo(ctx.session, ctx.account_id)
    done = await repo.copied_parents()
    items = KnowledgeItemsRepo(ctx.session, ctx.account_id)
    factor = ctx.config.seed.prior_factor
    eff = tuple(x * factor for x in ctx.config.priors.eff)
    fit = tuple(x * factor for x in ctx.config.priors.fit)
    now = ctx.clock.now()
    created: list[UUID] = []
    for parent in parents:
        if parent["id"] in done:
            continue
        fields: dict[str, Any] = {f: parent[f] for f in _COPY_FIELDS}
        item = await items.create(
            **fields,
            origin="seed",
            parent_item_id=parent["id"],
            created_at=now,
            updated_at=now,
            scopes=await BrainRepo(ctx.session, glob.id).item_scopes(parent["id"]),
            provenance={"parent_item_id": str(parent["id"])},
        )
        await repo.set_priors(item.id, eff=(eff[0], eff[1]), fit=(fit[0], fit[1]))
        created.append(item.id)
    return created
