"""Знания класса C для линтера (ТЗ v3.1, этап 11, задача 13): мягкие запреты → предупреждения."""

from __future__ import annotations

import re
from typing import Protocol
from uuid import UUID

from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.quality import QualityRepo
from xcopilot.db.uow import DbSession
from xcopilot.quality.lint.engine import SoftRule


class ClassCRules(Protocol):
    async def load(
        self, session: DbSession, account_id: UUID, lang: str | None
    ) -> list[SoftRule]: ...


class NoClassC:
    async def load(self, session: DbSession, account_id: UUID, lang: str | None) -> list[SoftRule]:
        return []


class DbClassCRules:
    """Рабочие знания класса C аккаунта и общего аккаунта: фразы в «…» / "…" из утверждения."""

    async def load(self, session: DbSession, account_id: UUID, lang: str | None) -> list[SoftRule]:
        glob = await AccountsRepo(session).get_global()
        repo = QualityRepo(session, account_id)
        rows = await repo.class_c_items(glob.id)
        confirmed = await repo.confirmed_ids([r[0] for r in rows])
        rules: list[SoftRule] = []
        seen: set[tuple[str, ...]] = set()
        for item_id, statement in rows:
            phrases = phrases_of(statement)
            if phrases and phrases not in seen:  # копия сида у аккаунта и оригинал — одно правило
                seen.add(phrases)
                rules.append(
                    SoftRule(
                        item_id=item_id,
                        statement=statement,
                        phrases=phrases,
                        confirmed=item_id in confirmed,
                    )
                )
        return rules


_QUOTED = re.compile(r"«([^«»]{2,60})»|\u201c([^\u201c\u201d]{2,60})\u201d|\"([^\"]{2,60})\"")
_PLACEHOLDER = re.compile(r"\b(?:X|Y|Z)\b|…|\.\.\.")


def phrases_of(statement: str) -> tuple[str, ...]:
    """Фразы в кавычках из утверждения знания; шаблоны с X/Y и многоточием пропускаются."""
    out: list[str] = []
    for m in _QUOTED.finditer(statement):
        phrase = next(g for g in m.groups() if g).strip()
        if phrase and not _PLACEHOLDER.search(phrase) and phrase not in out:
            out.append(phrase)
    return tuple(out)
