"""Петля ворот (этап 11, задачи 8–10): линтер → судья → решение, не больше двух переписываний.

Ход по варианту черновика:
1. Линтер (с автоисправлением регистра и хэштегов в той же ревизии). Ошибка → переписать,
   судья не вызывается.
2. Судья ворот №2 (резервный — пометка `fallback_judge`; судьи нет или он недоступен —
   режим «только линтер», пометка `lint_only`).
3. Политика: pass → `judged`; needs_owner → `needs_owner`; rewrite → новая ревизия
   (`parent_draft_id`), старая — `expired`. После двух переписываний — `needs_owner`.
4. Отчёты — `drafts.lint_report` / `drafts.judge_verdict`, итог — `lint_report.gate`;
   события `draft.lint_failed`, `draft.judged`, `draft.rewritten`, `draft.gate_decided`.
5. На финальной ревизии — детекторы знаний (`mark_applied`).

Повторный вызов на ту же ревизию (или на уже переписанную) возвращает сохранённое решение.
LLM вызывается вне транзакций.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from pydantic import BaseModel, Field, ValidationError

from xcopilot.clock import Clock
from xcopilot.db.repos import DraftsRepo, RunsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.quality import QualityRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import BudgetExceeded
from xcopilot.llm.errors import LLMOutputError, ProviderUnavailable
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.metrics.events import EventType, emit
from xcopilot.quality.attribution import AppliedDetector, NoDetector, mark_applied
from xcopilot.quality.instructions import to_rewrite_instructions
from xcopilot.quality.judge import JudgeParseError, JudgePort, JudgeVerdict, judge
from xcopilot.quality.knowledge import ClassCRules, DbClassCRules
from xcopilot.quality.lint.engine import (
    Finding,
    ForeignText,
    LintContext,
    LintDraft,
    Linter,
    LintReport,
)
from xcopilot.quality.policy import Decision, Thresholds, decide
from xcopilot.quality.settings import global_banned_phrases, hard_rules_of
from xcopilot.retrieval.context_pack import ContextPack, PackIds, rehydrate
from xcopilot.writer import render

log = structlog.get_logger()

REWRITE_PROMPT = "writer.rewrite.v1"
OWN_WINDOW = timedelta(days=60)
_NO_JUDGE = (ProviderUnavailable, BudgetExceeded, LLMOutputError, JudgeParseError)


class RewriteOut(BaseModel):
    text: str = Field(min_length=1, max_length=25_000)
    parts: list[str] = Field(default_factory=list, max_length=8)


class Rewriter(Protocol):
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
    ) -> RewriteOut: ...


class GatewayRewriter:
    """Переписчик через шлюз: роль writer, промпт `writer.rewrite.v1`."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

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
        sections = render.pack_sections(pack)
        body = text if not parts else "\n\n".join(parts)
        return await self._gateway.generate(
            role=Role.WRITER,
            prompt_key=REWRITE_PROMPT,
            variables={
                "passport": sections["passport"],
                "task": render.render_task(pack.task),
                "examples": sections.get("examples", ""),
                "facts": sections["facts"],
                "format": format,
                "draft": body,
                "instructions": "\n".join(f"- {line}" for line in instructions),
            },
            schema=RewriteOut,
            account_id=account_id,
            purpose="draft.rewrite",
            entity=("draft", draft_id),
        )


@dataclass
class _Ctx:
    pack: ContextPack
    lint: LintContext
    thresholds: Thresholds


def _user_input(context: Mapping[str, Any], pack: ContextPack) -> str:
    parts = [pack.task.topic]
    for key in ("hint", "target_text"):
        value = context.get(key)
        if isinstance(value, str):
            parts.append(value)
    trend = context.get("trend")
    if isinstance(trend, Mapping) and isinstance(trend.get("own_topic"), str):
        parts.append(trend["own_topic"])
    return "\n".join(parts)


def _restore(row: Any) -> Decision:
    report = row.lint_report or {}
    decision = Decision.model_validate(report["gate"])
    decision.lint = LintReport(
        text=row.text,
        parts=list(row.parts or []),
        findings=[Finding.model_validate(f) for f in report.get("findings", [])],
        autofixed=list(report.get("autofixed", [])),
    )
    if row.judge_verdict:
        decision.verdict = JudgeVerdict.model_validate(row.judge_verdict)
    return decision


class QualityGate:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        judge: JudgePort | None,
        rewriter: Rewriter,
        clock: Clock,
        linter: Linter | None = None,
        thresholds: Thresholds | None = None,
        class_c: ClassCRules | None = None,
        detector: AppliedDetector | None = None,
        global_banned: Sequence[str] | None = None,
    ) -> None:
        self._uow = uow_factory
        self._judge = judge
        self._rewriter = rewriter
        self._clock = clock
        self._linter = linter or Linter()
        self._thresholds = thresholds or Thresholds.load()
        self._class_c = class_c or DbClassCRules()
        self._detector = detector or NoDetector()
        self._banned = tuple(global_banned) if global_banned is not None else None

    # ── публичный API ───────────────────────────────────────────────────────────────────

    async def run_gate(self, account_id: UUID, draft_id: UUID) -> Decision:
        """Провести вариант через ворота; вернуть решение по финальной ревизии."""
        async with self._uow() as uow:
            leaf = await self._leaf(uow.session, account_id, draft_id)
            row = await DraftsRepo(uow.session, account_id).require(leaf)
            if row.lint_report and "gate" in row.lint_report:
                return _restore(row)
            ctx = await self._context(uow.session, account_id, row)
            text, parts, fmt, revision = row.text, list(row.parts or []), row.format, row.revision
        rewrites = revision - 1
        current = leaf
        while True:
            draft = LintDraft(text=text, format=fmt, parts=tuple(parts))
            report = await self._lint(account_id, current, draft, ctx)
            verdict: JudgeVerdict | None = None
            judged = False
            if report.ok and self._judge is not None:
                verdict = await self._ask_judge(account_id, current, report, fmt, ctx.pack)
                judged = verdict is not None
            decision = decide(report, verdict, ctx.thresholds)
            decision.draft_id, decision.revision, decision.rewrites = current, revision, rewrites
            if judged:
                await self._save_verdict(account_id, current, decision)
            if decision.outcome == "rewrite" and rewrites < ctx.thresholds.max_rewrites:
                new = await self._rewrite(account_id, current, report, fmt, decision, ctx.pack)
                if new is not None:
                    current, text, parts = new.id, new.text, list(new.parts or [])
                    revision, rewrites = new.revision, rewrites + 1
                    continue
                decision.flags.append("writer_unavailable")
            if decision.outcome == "rewrite":
                decision.outcome = "needs_owner"
                if "writer_unavailable" not in decision.flags:
                    decision.flags.append("max_rewrites")
            await self._finish(account_id, decision)
            return decision

    async def relint(
        self, account_id: UUID, draft_id: UUID, text: str, parts: Sequence[str]
    ) -> LintReport:
        """Линтер по тексту, исправленному владельцем (этап 12): контекст ворот, без записи."""
        raise NotImplementedError

    # ── шаги ────────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def _leaf(session: DbSession, account_id: UUID, draft_id: UUID) -> UUID:
        repo = QualityRepo(session, account_id)
        current = draft_id
        while (child := await repo.child_revision(current)) is not None:
            current = child
        return current

    async def _context(self, session: DbSession, account_id: UUID, row: Any) -> _Ctx:
        run = await RunsRepo(session, account_id).require(row.run_id)
        context = dict(run.context_pack or {})
        pack = await rehydrate(session, account_id, PackIds.model_validate(context))
        repo = QualityRepo(session, account_id)
        settings = await repo.account_settings()
        thresholds = self._thresholds.with_overrides(settings.get("thresholds"))
        own = await repo.own_recent(self._clock.now() - OWN_WINDOW)
        soft = await self._class_c.load(session, account_id, pack.task.lang)
        banned = self._banned if self._banned is not None else global_banned_phrases()
        lint = LintContext(
            hard_rules=hard_rules_of(pack.hard_rules),
            global_banned=tuple(banned),
            lang=pack.task.lang,
            facts=tuple(f.statement for f in pack.facts),
            user_input=_user_input(context, pack),
            own_recent=tuple(own),
            class_c=tuple(soft),
        )
        return _Ctx(pack=pack, lint=lint, thresholds=thresholds)

    async def _lint(
        self, account_id: UUID, draft_id: UUID, draft: LintDraft, ctx: _Ctx
    ) -> LintReport:
        async with self._uow() as uow:
            repo = QualityRepo(uow.session, account_id)
            foreign = [ForeignText(t, sid) for sid, t in await repo.foreign_candidates(draft.text)]
        report = self._linter.run(draft, replace(ctx.lint, foreign=tuple(foreign)))
        async with self._uow() as uow:
            drafts = DraftsRepo(uow.session, account_id)
            row = await drafts.require(draft_id)
            stored = dict(row.lint_report or {})
            stored.update(
                findings=[f.model_dump(mode="json") for f in report.findings],
                autofixed=report.autofixed,
            )
            values: dict[str, Any] = {"lint_report": stored}
            if report.autofixed:
                values.update(text=report.text, parts=report.parts)
            await drafts.update(draft_id, **values)
            for rule in dict.fromkeys(f.rule for f in report.errors):
                await emit(uow.session, account_id=account_id, type=EventType.DRAFT_LINT_FAILED,
                           entity=("draft", draft_id), props={"rule": rule})  # fmt: skip
            if report.ok:
                await drafts.set_status(draft_id, DraftStatus.LINTED)
        return report

    async def _ask_judge(
        self, account_id: UUID, draft_id: UUID, report: LintReport, fmt: str, pack: ContextPack
    ) -> JudgeVerdict | None:
        assert self._judge is not None
        draft = LintDraft(text=report.text, format=fmt, parts=tuple(report.parts))
        try:
            return await judge(
                draft, pack, port=self._judge, account_id=account_id, entity=("draft", draft_id)
            )
        except _NO_JUDGE as exc:
            log.warning(
                "quality.judge_unavailable", draft_id=str(draft_id), error=type(exc).__name__
            )
            return None

    async def _save_verdict(self, account_id: UUID, draft_id: UUID, decision: Decision) -> None:
        verdict = decision.verdict
        assert verdict is not None
        async with self._uow() as uow:
            await DraftsRepo(uow.session, account_id).update(
                draft_id, judge_verdict=verdict.model_dump(mode="json")
            )
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.DRAFT_JUDGED,
                entity=("draft", draft_id),
                props={
                    "verdict": "pass" if decision.outcome == "pass" else "fail",
                    "score": verdict.voice_match.score,
                },
            )

    async def _rewrite(
        self,
        account_id: UUID,
        draft_id: UUID,
        report: LintReport,
        fmt: str,
        decision: Decision,
        pack: ContextPack,
    ) -> Any:
        instructions = to_rewrite_instructions(decision, topic=pack.task.topic)
        try:
            out = await self._rewriter.rewrite(
                account_id=account_id,
                draft_id=draft_id,
                text=report.text,
                parts=report.parts,
                format=fmt,
                instructions=instructions,
                pack=pack,
            )
        except (ProviderUnavailable, BudgetExceeded, LLMOutputError, ValidationError) as exc:
            log.warning("quality.rewrite_failed", draft_id=str(draft_id), error=type(exc).__name__)
            return None
        async with self._uow() as uow:
            drafts = DraftsRepo(uow.session, account_id)
            new = await drafts.add_revision(draft_id, text=out.text, parts=out.parts)
            await drafts.set_status(draft_id, DraftStatus.EXPIRED)
            reason = decision.reasons[0].code if decision.reasons else "rewrite"
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.DRAFT_REWRITTEN,
                entity=("draft", new.id),
                props={"revision": new.revision, "reason": reason},
            )
        return new

    async def _finish(self, account_id: UUID, decision: Decision) -> None:
        assert decision.draft_id is not None
        draft_id = decision.draft_id
        async with self._uow() as uow:
            drafts = DraftsRepo(uow.session, account_id)
            row = await drafts.require(draft_id)
            if decision.outcome == "pass":
                if row.status == DraftStatus.GENERATED:
                    await drafts.set_status(draft_id, DraftStatus.LINTED)
                await drafts.set_status(draft_id, DraftStatus.JUDGED)
            else:
                await drafts.set_status(draft_id, DraftStatus.NEEDS_OWNER)
            stored = dict(row.lint_report or {})
            stored["gate"] = decision.model_dump(mode="json", exclude={"lint", "verdict"})
            await drafts.update(draft_id, lint_report=stored)
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.DRAFT_GATE_DECIDED,
                entity=("draft", draft_id),
                props={
                    "outcome": decision.outcome,
                    "revision": decision.revision,
                    "rewrites": decision.rewrites,
                    "flags": decision.flags,
                },
            )
            await mark_applied(
                uow.session,
                account_id,
                draft_id,
                detector=self._detector,
                now=self._clock.now(),
            )
