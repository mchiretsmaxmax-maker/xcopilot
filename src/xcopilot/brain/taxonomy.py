"""Словари разбора (ТЗ v3.1, раздел 2.1; этап 7A): стартовые значения, версия, предложения.

Общие значения живут в системном аккаунте global, свои — в аккаунте. Версия таксономии
аккаунта — наибольшая `since_version` среди действующих значений (global + свои):
подтверждение нового значения поднимает её на единицу.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from xcopilot.brain.schemas import ProposedTerm
from xcopilot.db.uow import DbSession

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


async def load_seed(session: DbSession, path: Path = SEED_PATH) -> int:
    raise NotImplementedError


async def active_terms(session: DbSession, account_id: UUID, facet: str) -> list[Term]:
    raise NotImplementedError


async def nearest_term(session: DbSession, account_id: UUID, facet: str, key: str) -> str:
    raise NotImplementedError


async def propose_term(session: DbSession, account_id: UUID, term: ProposedTerm) -> Term:
    raise NotImplementedError


async def proposed_terms(session: DbSession, account_id: UUID) -> list[Term]:
    raise NotImplementedError


async def accept_term(session: DbSession, account_id: UUID, term_id: UUID, user_id: UUID) -> int:
    raise NotImplementedError


async def current_version(session: DbSession, account_id: UUID) -> int:
    raise NotImplementedError
