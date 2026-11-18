"""Разбор материала ролью cheap (ТЗ v3.1, шаги 3–6 конвейера; этап 7A).

Берёт только `sources.ready_for_analysis()`; материал идёт в промпт как недоверенные данные;
цитаты проверяет код (`verify_quotes`); неизвестный ключ словаря заменяется ближайшим и
уходит в предложения. Переразбор создаёт новую текущую строку, старая остаётся в истории.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import structlog

from xcopilot.bot import texts
from xcopilot.brain import batch, outcomes, signals, taxonomy
from xcopilot.brain.card import CardView, render_card
from xcopilot.brain.config import BrainConfig
from xcopilot.brain.features import compute_features
from xcopilot.brain.schemas import MaterialAnalysis, ProposedTerm, verify_quotes
from xcopilot.clock import Clock
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.repos.llm_calls import LlmCallsRepo
from xcopilot.db.repos.materials import (
    MaterialAnalysesRepo,
    MaterialOutcomesRepo,
    MaterialSignalsRepo,
)
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.llm.errors import LLMOutputError
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.metrics.events import EventType, emit

log = structlog.get_logger(__name__)

PROMPT_KEY = "brain.analyze_material.v1"
ANALYZER_VERSION = PROMPT_KEY
ANALYZE_KIND = "brain.analyze"
PURPOSE = "brain.analyze"
REANALYZE_PURPOSE = "brain.reanalyze"
FORMATS = ("post", "thread", "reply", "quote", "article")
# Поле разбора → словарь (одиночные и списочные ключи).
SINGLE_FACETS = {f: f for f in ("archetype", "hook_type", "structure", "closer")}
LIST_FACETS = {"tone": "tone", "devices": "device"}
_PROPOSED_NOTE = "Предложено моделью при разборе: ключа нет в словаре"


class CardNotifier(Protocol):
    async def notify(self, account_id: UUID, text: str, keyboard: Any = None) -> int: ...


@dataclass(frozen=True)
class _Prepared:
    source: Any  # строка sources (модель ORM не импортируется вне db)
    text: str
    features: dict[str, Any]
    variables: dict[str, str]
    taxonomy_version: int
    active: dict[str, dict[str, str]]  # facet → key → название


class Analyzer:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        clock: Clock,
        *,
        notifier: CardNotifier | None = None,
        config: BrainConfig | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._clock = clock
        self._notifier = notifier
        self._config = config or BrainConfig.load()
        self._model = ModelsConfig.load().roles[Role.CHEAP].model
        self._seeded = False

    async def analyze(self, account_id: UUID, source_id: UUID) -> MaterialAnalysis | None:
        """Разобрать принятый материал; не готовый к разбору — None, модель не зовётся."""
        async with self._uow() as uow:
            if not await SourcesRepo(uow.session, account_id).is_ready_for_analysis(source_id):
                return None
        result = await self._run(account_id, source_id, PURPOSE)
        if result is not None:
            await self._send_card(account_id, source_id)
        return result

    async def reanalyze_outdated(self, account_id: UUID, limit: int = 50) -> int:
        """Переразобрать материалы со старым промптом или словарём в пределах дневного лимита.

        Вернуть число переразобранных. Лимит — расход целей brain.reanalyze за сутки (UTC).
        """
        async with self._uow() as uow:
            version = await taxonomy.current_version(uow.session, account_id)
            ids = await MaterialAnalysesRepo(uow.session, account_id).outdated(
                ANALYZER_VERSION, version, limit
            )
        now = self._clock.now().astimezone(UTC)
        day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        done = 0
        for sid in ids:
            async with self._uow() as uow:
                spent = await LlmCallsRepo(uow.session, account_id).spent(
                    REANALYZE_PURPOSE, day_start
                )
            if spent >= self._config.reanalyze_daily_limit_micro:
                log.info("brain.reanalyze.limit", account_id=str(account_id), spent=spent)
                break
            if await self._run(account_id, sid, REANALYZE_PURPOSE) is not None:
                done += 1
        return done

    # --- внутреннее ----------------------------------------------------------------------

    async def _run(
        self, account_id: UUID, source_id: UUID, purpose: str
    ) -> MaterialAnalysis | None:
        async with self._uow() as uow:
            prep = await self._prepare(uow.session, account_id, source_id)
        try:
            raw = await self._gateway.generate(
                role=Role.CHEAP,
                prompt_key=PROMPT_KEY,
                variables=prep.variables,
                schema=MaterialAnalysis,
                account_id=account_id,
                purpose=purpose,
                entity=("source", source_id),
            )
        except LLMOutputError:
            log.warning("brain.analyze.bad_output", source_id=str(source_id))
            if purpose == PURPOSE:
                async with self._uow() as uow:
                    await SourcesRepo(uow.session, account_id).update(
                        source_id, analysis_status="failed"
                    )
            return None
        checked = verify_quotes(raw, prep.text)
        async with self._uow() as uow:
            result = await self._normalize(uow.session, account_id, checked, prep)
            await self._save(uow.session, account_id, prep, result, purpose)
        return result

    async def _prepare(self, session: DbSession, account_id: UUID, source_id: UUID) -> _Prepared:
        if not self._seeded:  # определения словарей — один раз на процесс (идемпотентно)
            await taxonomy.load_seed(session)
            self._seeded = True
        sources = SourcesRepo(session, account_id)
        src = await sources.require(source_id)
        docs = await sources.documents(source_id)
        parts = [d.text_clean or d.text for d in docs]
        text = "\n\n".join(parts)
        meta = src.meta or {}
        features = compute_features(
            text,
            src.kind,
            parts=parts if src.kind == "thread" and len(parts) > 1 else None,
            has_media=bool(meta.get("has_media")),
        )
        active: dict[str, dict[str, str]] = {}
        variables: dict[str, str] = {}
        for facet in taxonomy.CRAFT_FACETS:
            terms = await taxonomy.active_terms(session, account_id, facet)
            active[facet] = {t.key: t.title for t in terms}
            variables[f"taxonomy_{facet}"] = "; ".join(
                f"{t.key} ({t.title}): {t.description}" if t.description else t.key for t in terms
            )
        parent = await self._parent_text(sources, src)
        variables.update(
            features_json=json.dumps(features, ensure_ascii=False),
            outcome=await self._outcome_text(session, account_id, source_id),
            parent=wrap_untrusted(parent, "parent") if parent else "",
            kind=src.kind,
            stance=src.stance,
            material=wrap_untrusted(text, "material"),
        )
        return _Prepared(
            source=src,
            text=text,
            features=features,
            variables=variables,
            taxonomy_version=await taxonomy.current_version(session, account_id),
            active=active,
        )

    @staticmethod
    async def _parent_text(sources: SourcesRepo, src: Any) -> str:
        if src.parent_source_id is not None:
            docs = await sources.documents(src.parent_source_id)
            if docs:
                return "\n\n".join(d.text_clean or d.text for d in docs)
        return str((src.meta or {}).get("parent_text") or "")

    @staticmethod
    async def _outcome_text(session: DbSession, account_id: UUID, source_id: UUID) -> str:
        cur = await MaterialOutcomesRepo(session, account_id).current(source_id)
        if cur is None or cur.basis in ("none", "owner_pick"):
            return "unknown"
        return (
            f"reach={cur.tier_reach}, engagement={cur.tier_engagement},"
            f" replies={cur.tier_replies}, saves={cur.tier_saves} (basis={cur.basis})"
        )

    async def _normalize(
        self,
        session: DbSession,
        account_id: UUID,
        analysis: MaterialAnalysis,
        prep: _Prepared,
    ) -> MaterialAnalysis:
        """Ключи вне действующего словаря → ближайший ключ; неизвестный ключ — в предложения."""
        for proposal in analysis.proposed_terms:
            await taxonomy.propose_term(session, account_id, proposal)
        offered: set[tuple[str, str]] = {(t.facet, t.key) for t in analysis.proposed_terms}

        async def fix(facet: str, key: str) -> str:
            if key in prep.active[facet]:
                return key
            if (facet, key) not in offered:
                offered.add((facet, key))
                await taxonomy.propose_term(
                    session,
                    account_id,
                    ProposedTerm.model_construct(
                        facet=facet,  # type: ignore[arg-type]
                        key=key,
                        title=key.replace("_", " "),
                        description=_PROPOSED_NOTE,
                        quote=analysis.hook_text or prep.text[:200],
                    ),
                )
            return await taxonomy.nearest_term(session, account_id, facet, key)

        update: dict[str, Any] = {}
        for field_name, facet in SINGLE_FACETS.items():
            update[field_name] = await fix(facet, getattr(analysis, field_name))
        for field_name, facet in LIST_FACETS.items():
            fixed: list[str] = []
            for key in getattr(analysis, field_name):
                new = await fix(facet, key)
                if new not in fixed:
                    fixed.append(new)
            update[field_name] = fixed
        if prep.source.kind in FORMATS:
            update["format"] = prep.source.kind
        return analysis.model_copy(update=update)

    async def _save(
        self,
        session: DbSession,
        account_id: UUID,
        prep: _Prepared,
        result: MaterialAnalysis,
        purpose: str,
    ) -> None:
        sid = prep.source.id
        call = await LlmCallsRepo(session, account_id).latest_for("source", sid, purpose)
        row = await MaterialAnalysesRepo(session, account_id).add_current(
            sid,
            analyzer_version=ANALYZER_VERSION,
            taxonomy_version=prep.taxonomy_version,
            model=call.model if call is not None else self._model,
            llm_call_id=call.id if call is not None else None,
            format=result.format,
            archetype=result.archetype,
            lang=result.lang,
            hook_type=result.hook_type,
            hook_text=result.hook_text,
            structure=result.structure,
            structure_outline=[
                {"step": i + 1, "role": s.role, "quote": s.quote}
                for i, s in enumerate(result.outline)
            ],
            tone=result.tone,
            devices=result.devices,
            closer=result.closer,
            human_markers=result.human_markers,
            ai_markers=result.ai_markers,
            features=prep.features,
            why_hypotheses=[w.model_dump() for w in result.why],
            declared_claims=[c.model_dump() for c in result.declared_claims],
            proposed_terms=[t.model_dump() for t in result.proposed_terms],
            quality_flags=list(result.quality_flags),
        )
        sources = SourcesRepo(session, account_id)
        if prep.source.analysis_status == "pending":
            await sources.update(sid, analysis_status="analyzed")
        await emit(
            session,
            account_id=account_id,
            type=EventType.MATERIAL_ANALYZED,
            entity=("source", sid),
            props={
                "source_id": str(sid),
                "analysis_id": str(row.id),
                "format": result.format,
                "analyzer_version": ANALYZER_VERSION,
            },
        )
        now = self._clock.now()
        await signals.default_pick(session, account_id, sid, observed_at=now)
        await outcomes.compute_outcome(session, account_id, sid, now=now)

    async def _send_card(self, account_id: UUID, source_id: UUID) -> None:
        if self._notifier is None:
            return
        if await batch.after_analysis(
            self._uow, self._notifier, account_id, source_id, now=self._clock.now()
        ):
            return  # материал из пачки: одна сводка вместо карточки на каждый (ADR 0033)
        async with self._uow() as uow:
            view = await card_view(uow.session, account_id, source_id)
        if view is None:
            return
        text, keyboard = render_card(view)
        await self._notifier.notify(account_id, text, keyboard)


async def card_view(session: DbSession, account_id: UUID, source_id: UUID) -> CardView | None:
    """Данные карточки из текущего разбора и исхода; своя история карточку не получает."""
    src = await SourcesRepo(session, account_id).require(source_id)
    if src.origin == "history_backfill":
        return None
    row = await MaterialAnalysesRepo(session, account_id).current(source_id)
    if row is None:
        return None

    async def titles(facet: str) -> dict[str, str]:
        return {t.key: t.title for t in await taxonomy.active_terms(session, account_id, facet)}

    hooks, structures, devices = (
        await titles("hook_type"),
        await titles("structure"),
        await titles("device"),
    )
    outcome = await MaterialOutcomesRepo(session, account_id).current(source_id)
    why = row.why_hypotheses[0]["claim"] if row.why_hypotheses else None
    auto = None
    if (
        outcome is not None
        and outcome.signal_origin == outcomes.AUTO_ORIGIN
        and outcome.basis == "rates"
    ):
        best = await MaterialSignalsRepo(session, account_id).best(source_id)
        likes = best.likes if best is not None and best.origin == outcomes.AUTO_ORIGIN else None
        auto = texts.material_auto_outcome(outcome.tier_engagement, likes)
    return CardView(
        source_id=source_id,
        format=row.format,
        author_handle=src.author_handle if src.stance != "own" else None,
        hook_title=hooks.get(row.hook_type or "", row.hook_type),
        hook_text=row.hook_text or "",
        structure_title=structures.get(row.structure or "", row.structure),
        line_count=int(row.features.get("line_count", 0)),
        device_titles=[devices.get(d, d) for d in row.devices],
        why=why,
        new_observations=len(row.proposed_terms),
        outcome_known=outcome is not None and outcome.basis in ("own_baseline", "rates"),
        auto_outcome=auto,
    )
