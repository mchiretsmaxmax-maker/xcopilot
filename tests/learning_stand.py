"""Стенд этапа 13: аккаунт с владельцем, ревью «до/после», кандидаты от подделки writer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import (
    NOW,
    FakeBrainLLM,
    candidate_judge,
    knowledge,
    make_ctx,
    new_account,
)
from tests.integration.db.brain_helpers import draft, insert, pack, user
from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import ExtractionResult, KnowledgeCandidate
from xcopilot.db.repos import AccountsRepo

LETS = "Let's dive into how L2 fees dropped 40% this month"
LETS_AFTER = "L2 fees dropped 40% this month"


@dataclass
class Learn:
    session: AsyncSession
    acc: UUID
    uid: UUID
    llm: FakeBrainLLM
    ctx: BrainContext
    n: int = 0

    async def review(
        self,
        action: str = "edit",
        *,
        before: str = LETS,
        after: str | None = LETS_AFTER,
        reason: str | None = None,
        distance: float | None = 0.2,
        at: datetime | None = None,
        draft_id: UUID | None = None,
        comment: str | None = None,
    ) -> UUID:
        """Строка reviews; время по умолчанию — «до NOW» с шагом в минуту."""
        self.n += 1
        when = at or NOW - timedelta(days=2) + timedelta(minutes=self.n)
        d = draft_id or await draft(self.session, self.acc)
        return await insert(
            self.session,
            "reviews",
            account_id=self.acc,
            draft_id=d,
            user_id=self.uid,
            action=action,
            reason_code=reason,
            text_before=before,
            text_after=after if action == "edit" else None,
            edit_distance=distance,
            created_at=when,
            comment=comment,
        )

    async def apply_item(self, item_id: UUID, review_id: UUID, *, applied: bool = True) -> None:
        """Знание было в пакете черновика, к которому относится ревью."""
        d = await self.session.scalar(
            text("SELECT draft_id FROM reviews WHERE id = :r"), {"r": review_id}
        )
        p = await pack(self.session, self.acc)
        await insert(
            self.session,
            "knowledge_applications",
            account_id=self.acc,
            pack_id=p,
            item_id=item_id,
            draft_id=d,
            slot="core",
            applied=applied,
        )

    async def item(self, **kw: Any) -> UUID:
        return await knowledge(self.session, self.acc, **kw)

    async def scalar(self, sql: str, **params: Any) -> Any:
        return await self.session.scalar(text(sql), {"a": self.acc, **params})

    async def edit_items(self) -> list[dict[str, Any]]:
        rows = await self.session.execute(
            text(
                "SELECT id, layer, status, owner_state, risk_class, statement FROM knowledge_items"
                " WHERE account_id = :a AND origin = 'edits' ORDER BY created_at, id"
            ),
            {"a": self.acc},
        )
        return [dict(r._mapping) for r in rows.all()]


async def make_learn(session: AsyncSession) -> Learn:
    acc = await new_account(session)
    uid = await user(session)
    await AccountsRepo(session).add_member(acc, uid, role="owner")
    llm = FakeBrainLLM(judge=candidate_judge(relation="different"))
    return Learn(session, acc, uid, llm, make_ctx(session, acc, llm=llm))


def lexicon_candidate(**kw: Any) -> KnowledgeCandidate:
    """Голос: не начинать пост с «let's» (supporting — метки правок M1, M2…)."""
    base: dict[str, Any] = {
        "kind": "anti_pattern",
        "layer": "voice",
        "element": "hook",
        "statement": "Ты убираешь «let's» в начале поста",
        "guidance": "Не начинай пост с «let's»",
        "target_metric": "quality",
        "detector": {"type": "none"},
        "supporting": ["M1", "M2"],
        "rationale": "В двух правках убрана фраза в начале поста",
    }
    base.update(kw)
    return KnowledgeCandidate.model_validate(base)


def humanness_candidate(**kw: Any) -> KnowledgeCandidate:
    base: dict[str, Any] = {
        "kind": "anti_pattern",
        "layer": "humanness",
        "element": "hook",
        "statement": "Фраза «let's dive into» звучит как сгенерированная",
        "guidance": "Не пиши «let's dive into»",
        "target_metric": "quality",
        "detector": {"type": "none"},
        "supporting": ["M1", "M2"],
        "rationale": "Владелец дважды убрал эту фразу",
    }
    base.update(kw)
    return KnowledgeCandidate.model_validate(base)


def result(*cands: KnowledgeCandidate) -> ExtractionResult:
    return ExtractionResult(candidates=list(cands))
