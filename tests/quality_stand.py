"""Стенд ворот качества (этап 11): стенд писателя + судья, переписчик и детектор по сценарию."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.writer_stand import WriterStand, draft_reply, make_writer_stand, post_task
from xcopilot.db.uow import DbSession
from xcopilot.llm.schemas import ChoiceA, NoulA, Question, ScoreA, Verdict
from xcopilot.quality.attribution import AppliedHit
from xcopilot.quality.loop import QualityGate, RewriteOut
from xcopilot.retrieval.context_pack import ContextPack

SF = async_sessionmaker[AsyncSession]

CLEAN = "блобы снизили комиссии роллапов, и это видно по моим транзакциям за неделю"
CLEAN2 = "перенёс кошелёк на l2 и забыл про газ, мосты теперь скучные и тихие"
CLEAN3 = "старый мост закрылся без шума, зато ликвидность ушла в новые сети за сутки"


def judge_answers(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "voice_match": ScoreA(score=2.6, confidence=0.9, probabilities={2: 0.4, 3: 0.6}),
        "ai_smell": ScoreA(score=2.8, confidence=0.9, probabilities={2: 0.2, 3: 0.8}),
        "hook": ScoreA(score=1.5, confidence=0.8, probabilities={1: 0.5, 2: 0.5}),
        "on_topic": NoulA(p=0.95),
        "grounded": NoulA(p=0.9),
        "risky": NoulA(p=0.05),
        "main_issue": ChoiceA(choice="none", confidence=0.8, probabilities={"none": 0.8}),
    }
    base.update(over)
    return base


def good(*, fallback: bool = False) -> Verdict:
    return Verdict(answers=judge_answers(), judge="judge-model", fallback=fallback)


def weak_voice() -> Verdict:
    low = ScoreA(score=1.2, confidence=0.9, probabilities={1: 0.8, 2: 0.2})
    issue = ChoiceA(choice="voice", confidence=0.9, probabilities={"voice": 0.9})
    return Verdict(answers=judge_answers(voice_match=low, main_issue=issue), judge="judge-model")


@dataclass
class JudgeCall:
    state: str
    questions: dict[str, Question]
    purpose: str
    entity: tuple[str, UUID] | None


@dataclass
class ScriptedJudge:
    """JudgePort по сценарию: Verdict или исключение на каждый вызов."""

    script: list[Verdict | Exception] = field(default_factory=list)
    calls: list[JudgeCall] = field(default_factory=list)

    def push(self, *items: Verdict | Exception) -> ScriptedJudge:
        self.script.extend(items)
        return self

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict:
        self.calls.append(JudgeCall(state, dict(questions), purpose, entity))
        if not self.script:
            raise AssertionError("ScriptedJudge: сценарий закончился")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@dataclass
class ScriptedRewriter:
    """Rewriter по сценарию: тексты новых ревизий по порядку."""

    script: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def push(self, *texts: str) -> ScriptedRewriter:
        self.script.extend(texts)
        return self

    async def rewrite(
        self,
        *,
        account_id: UUID,
        draft_id: UUID,
        text: str,
        parts: Sequence[str],
        format: str,
        instructions: Sequence[str],
        pack: ContextPack,
    ) -> RewriteOut:
        self.calls.append(
            {"draft_id": draft_id, "text": text, "instructions": list(instructions), "pack": pack}
        )
        if not self.script:
            raise AssertionError("ScriptedRewriter: сценарий закончился")
        return RewriteOut(text=self.script.pop(0))


@dataclass
class FakeDetector:
    """AppliedDetector: заданные знания «применены», остальные — нет."""

    applied: set[UUID] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)

    async def detect(
        self, session: DbSession, account_id: UUID, text: str, item_ids: Sequence[UUID]
    ) -> dict[UUID, AppliedHit]:
        self.texts.append(text)
        return {i: AppliedHit(i in self.applied, 0.9 if i in self.applied else 0.1, "rubric")
                for i in item_ids}  # fmt: skip


@dataclass
class GateStand:
    ws: WriterStand
    judge: ScriptedJudge
    rewriter: ScriptedRewriter
    detector: FakeDetector
    gate: QualityGate

    @property
    def account_id(self) -> UUID:
        return self.ws.account_id

    async def draft(self, text: str = CLEAN) -> UUID:
        """Один черновик (ревизия 1) через настоящего писателя этапа 10."""
        self.ws.writer.push(draft_reply(text))
        run = await self.ws.service.generate(self.account_id, post_task(), n=1)
        return run.drafts[0].draft_id

    async def run(self, draft_id: UUID) -> Any:
        return await self.gate.run_gate(self.account_id, draft_id)

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        return await self.ws.rows(sql, **params)

    async def count(self, table: str, where: str = "true", **params: Any) -> int:
        return await self.ws.count(table, where, **params)


def make_gate(ws: WriterStand, judge: Any, rewriter: Any, detector: Any) -> QualityGate:
    return QualityGate(
        uow_factory=ws.uow,
        judge=judge,
        rewriter=rewriter,
        clock=ws.clock,
        detector=detector,
    )


async def make_gate_stand(
    sf: SF, *, no_judge: bool = False, handle: str | None = None
) -> GateStand:
    """Свой аккаунт на каждый стенд (handle уникален): в одном тесте можно поднять два стенда."""
    ws = await make_writer_stand(sf, handle=handle or f"gate_{uuid4().hex[:10]}")
    judge, rewriter, detector = ScriptedJudge(), ScriptedRewriter(), FakeDetector()
    gate = make_gate(ws, None if no_judge else judge, rewriter, detector)
    return GateStand(ws=ws, judge=judge, rewriter=rewriter, detector=detector, gate=gate)
