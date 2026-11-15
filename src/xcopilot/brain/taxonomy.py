"""Словари разбора (ТЗ v3.1, раздел 2.1; этап 7A): стартовые значения, версия, предложения.

Общие значения живут в системном аккаунте global, свои — в аккаунте. Версия таксономии
аккаунта — наибольшая `since_version` среди действующих значений (global + свои):
подтверждение нового значения поднимает её на единицу.
"""

from __future__ import annotations

import difflib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from xcopilot.brain.schemas import ProposedTerm
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.materials import TaxonomyRepo
from xcopilot.db.uow import DbSession
from xcopilot.errors import NotFound
from xcopilot.metrics.events import EventType, emit

SEED_PATH = Path(__file__).resolve().parents[3] / "config" / "taxonomy.toml"
CRAFT_FACETS = ("archetype", "hook_type", "structure", "device", "closer", "tone")


@dataclass(frozen=True)
class Term:
    id: UUID
    account_id: UUID
    facet: str
    key: str
    title: str
    description: str
    status: str
    since_version: int
    created_by: str


def _term(row: Any) -> Term:
    return Term(
        id=row.id,
        account_id=row.account_id,
        facet=row.facet,
        key=row.key,
        title=row.title,
        description=row.description,
        status=row.status,
        since_version=row.since_version,
        created_by=row.created_by,
    )


def read_seed(path: Path = SEED_PATH) -> dict[str, dict[str, tuple[str, str]]]:
    """Словари из файла: facet → key → (название, определение)."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        facet: {key: (str(v[0]), str(v[1])) for key, v in values.items()}
        for facet, values in raw.items()
    }


async def load_seed(session: DbSession, path: Path = SEED_PATH) -> int:
    """Стартовые значения в системном аккаунте global; вернуть число вставленных.

    Идемпотентно: засеянное миграцией 0005 не вставляется повторно, а получает название и
    определение из файла, если они пустые. Версия таксономии не меняется.
    """
    global_acc = await AccountsRepo(session).get_global()
    repo = TaxonomyRepo(session, global_acc.id)
    inserted = 0
    for facet, terms in read_seed(path).items():
        for key, (title, description) in terms.items():
            inserted += await repo.fill_seed(facet, key, title, description)
    return inserted


async def active_terms(session: DbSession, account_id: UUID, facet: str) -> list[Term]:
    """Действующие значения словаря для аккаунта: общие и свои подтверждённые."""
    return [_term(t) for t in await TaxonomyRepo(session, account_id).visible(facet)]


async def nearest_term(session: DbSession, account_id: UUID, facet: str, key: str) -> str:
    """Ближайший действующий ключ словаря (сходство строк); точное совпадение — сам ключ."""
    keys = [t.key for t in await active_terms(session, account_id, facet)]
    if not keys:
        raise NotFound(f"словарь {facet} пуст")
    if key in keys:
        return key
    best = difflib.get_close_matches(key, keys, n=1, cutoff=0.0)
    return best[0] if best else keys[0]


async def propose_term(session: DbSession, account_id: UUID, term: ProposedTerm) -> Term:
    """Предложение модели: своё значение аккаунта со статусом proposed (без дублей).

    Ключ, который уже действует (общий или свой), не предлагается — возвращается он сам.
    """
    repo = TaxonomyRepo(session, account_id)
    own = await repo.by_key(term.facet, term.key)
    if own is not None:
        return _term(own)
    for active in await repo.visible(term.facet):
        if active.key == term.key:
            return _term(active)
    row = await repo.add(
        facet=term.facet,
        key=term.key,
        title=term.title,
        description=term.description,
        status="proposed",
        created_by="model",
        since_version=await repo.max_version(),
    )
    await emit(
        session,
        account_id=account_id,
        type=EventType.TAXONOMY_TERM_PROPOSED,
        entity=("taxonomy_term", row.id),
        props={"facet": term.facet, "key": term.key},
    )
    return _term(row)


async def proposed_terms(session: DbSession, account_id: UUID) -> list[Term]:
    """Предложения аккаунта, ждущие решения владельца."""
    return [_term(t) for t in await TaxonomyRepo(session, account_id).by_status("proposed")]


async def accept_term(session: DbSession, account_id: UUID, term_id: UUID, user_id: UUID) -> int:
    """Подтвердить предложение: значение начинает действовать с новой версии таксономии.

    Вернуть новую версию. Уже действующее значение версию не меняет.
    """
    repo = TaxonomyRepo(session, account_id)
    row = await repo.require(term_id)
    if row.status == "active":
        return await repo.max_version()
    version = await repo.max_version() + 1
    await repo.update(term_id, status="active", since_version=version, created_by="owner")
    await emit(
        session,
        account_id=account_id,
        type=EventType.TAXONOMY_TERM_ACCEPTED,
        actor="user",
        user_id=user_id,
        entity=("taxonomy_term", term_id),
        props={"facet": row.facet, "key": row.key, "version": version},
    )
    return version


async def current_version(session: DbSession, account_id: UUID) -> int:
    """Версия таксономии аккаунта."""
    return await TaxonomyRepo(session, account_id).max_version()
