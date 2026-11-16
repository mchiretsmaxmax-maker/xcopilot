"""Генерация черновиков: три варианта в голосе аккаунта с пакетом и знаниями (этап 10).

Ход запуска:
1. Пакет этапа 9 (`build_pack`: паспорт, примеры, знания, факты, антипримеры), испытуемое
   знание (`ProbationPicker`), строка `knowledge_packs` (`PackRecorder`), строка
   `generation_runs` (версия паспорта, версия промпта, id пакетов) — одна транзакция.
2. Писатель (роль writer, замены нет): без испытуемого — один вызов на n вариантов; с ним —
   вызов на n−1 обычных и отдельный вызов на вариант 🧪, так испытуемое знание не попадает в
   промпт обычных вариантов.
3. Хвост тренда: вариант, похожий на оригинал (≥ 0.6), перегенерируется один раз, иначе
   отбрасывается. Похожие между собой варианты (≥ 0.8) — одна перегенерация.
4. Черновики, `knowledge_applications` (каждое знание × каждый вариант, 🧪 — только у своего),
   завершение запуска — одна транзакция.

Повтор задачи очереди (`job_id`) не создаёт второй набор: готовый запуск возвращается как есть.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, DraftsRepo, PromptsRepo, RunsRepo
from xcopilot.db.repos.knowledge import KnowledgeApplicationsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.errors import BudgetExceeded
from xcopilot.llm.errors import LLMOutputError, PermanentProviderError, ProviderUnavailable
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.roles import Role
from xcopilot.metrics.events import EventType, emit
from xcopilot.retrieval.context_pack import (
    ContextPack,
    KnowledgeItem,
    KnowledgeProvider,
    TaskSpec,
    build_pack,
)
from xcopilot.writer import render
from xcopilot.writer.formats import FORMATS, FormatCheck, normalize_parts, split_post
from xcopilot.writer.formats import validate_format as check_format
from xcopilot.writer.knowledge import DbPackRecorder, PackRecorder
from xcopilot.writer.probation import NoProbation, ProbationPicker
from xcopilot.writer.schemas import (
    DraftBatch,
    DraftOut,
    DraftView,
    GenerationRun,
    TargetPost,
    WhyItWorked,
)
from xcopilot.writer.similarity import ensure_distinct
from xcopilot.writer.trend_tail import PostRef, analyze, is_near_copy, resolve_post
from xcopilot.x.intents import MAX_WEIGHTED_LEN, weighted_len

DRAFT_PROMPT = "writer.draft.v1"
TAIL_PROMPT = "writer.trend_tail.write.v1"

__all__ = ["DRAFT_PROMPT", "TAIL_PROMPT", "WriterInputError", "WriterService", "ensure_distinct"]


class WriterInputError(ValueError):
    """Задача писателю составлена неверно (например, ответ без целевого поста)."""


@dataclass
class _Spec:
    kind: str
    prompt_key: str
    purpose: str
    task: TaskSpec
    hint: str | None = None
    topic_id: UUID | None = None
    target: TargetPost | None = None
    original: str | None = None
    analysis: WhyItWorked | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Prepared:
    run_id: UUID
    pack: ContextPack
    sections: dict[str, str]
    items: list[KnowledgeItem]
    probation: KnowledgeItem | None
    pack_id: UUID | None


@dataclass
class _Variant:
    out: DraftOut
    probation: KnowledgeItem | None = None


class WriterService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        embedder: Embedder,
        clock: Clock,
        knowledge: KnowledgeProvider | None = None,
        probation: ProbationPicker | None = None,
        prompts: PromptRegistry | None = None,
        pack_recorder: PackRecorder | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._embedder = embedder
        self._clock = clock
        self._knowledge = knowledge
        self._probation = probation or NoProbation()
        self._prompts = prompts or PromptRegistry()
        self._recorder = pack_recorder or DbPackRecorder()

    # ── публичный API ───────────────────────────────────────────────────────────────────

    async def generate(
        self,
        account_id: UUID,
        task: TaskSpec,
        n: int = 3,
        hint: str | None = None,
        *,
        topic_id: UUID | None = None,
        target: TargetPost | None = None,
        job_id: UUID | None = None,
    ) -> GenerationRun:
        """n вариантов черновика под задачу (формат post / thread / reply / quote)."""
        spec = FORMATS.get(task.format)
        if spec is None:
            raise WriterInputError(f"неизвестный формат: {task.format}")
        if spec.needs_target and target is None:
            raise WriterInputError("ответ и цитата пишутся только к целевому посту")
        return await self._run(
            account_id,
            _Spec(
                kind=task.format,
                prompt_key=DRAFT_PROMPT,
                purpose="writer.generate",
                task=task,
                hint=hint,
                topic_id=topic_id,
                target=target,
                extra={
                    "target_url": target.url if target else None,
                    "target_text": target.text if target else None,  # ввод для ворот (этап 11)
                },
            ),
            n,
            job_id,
        )

    async def generate_tail(
        self,
        account_id: UUID,
        post_ref: PostRef,
        own_topic: str | None = None,
        *,
        n: int = 3,
        job_id: UUID | None = None,
    ) -> GenerationRun:
        """«Хвост тренда»: механика чужого поста → новый текст о своей теме своим голосом."""
        async with self._uow() as uow:
            post = await resolve_post(uow.session, account_id, post_ref)
            why = await analyze(uow.session, account_id, post)
            account = await AccountsRepo(uow.session).get(account_id)
        topic = (own_topic or "").strip() or post.text[:200].strip()
        task = TaskSpec(
            format="post",
            lang=account.lang if account is not None else "ru",
            niche=account.niche if account is not None else None,
            topic=topic.ljust(3, "…"),
        )
        spec = _Spec(
            kind="trend_tail",
            prompt_key=TAIL_PROMPT,
            purpose="writer.trend_tail",
            task=task,
            original=post.text,
            analysis=why,
            extra={
                "trend": {
                    "source_id": str(post.source_id) if post.source_id else None,
                    "url": post.url,
                    "analysis_id": str(why.analysis_id) if why.analysis_id else None,
                    "own_topic": own_topic,
                }
            },
        )
        return await self._run(account_id, spec, n, job_id)

    # ── запуск ──────────────────────────────────────────────────────────────────────────

    async def _run(
        self, account_id: UUID, spec: _Spec, n: int, job_id: UUID | None
    ) -> GenerationRun:
        if n < 1:
            raise WriterInputError("нужен хотя бы один вариант")
        if job_id is not None:
            done = await self._finished_run(account_id, job_id, spec)
            if done is not None:
                return done
        prep = await self._prepare(account_id, spec, job_id)
        try:
            variants, warnings = await self._write(account_id, spec, prep, n)
        except BudgetExceeded:
            await self._fail(account_id, prep.run_id, spec.kind, "budget_exceeded")
            raise
        except ProviderUnavailable:
            await self._fail(account_id, prep.run_id, spec.kind, "writer_unavailable")
            raise
        except (LLMOutputError, PermanentProviderError):
            await self._fail(account_id, prep.run_id, spec.kind, "writer_error")
            raise
        return await self._save(account_id, spec, prep, variants, warnings)

    async def _finished_run(
        self, account_id: UUID, job_id: UUID, spec: _Spec
    ) -> GenerationRun | None:
        async with self._uow() as uow:
            run = await RunsRepo(uow.session, account_id).by_job(job_id)
            if run is None or run.status != "done":
                return None
            drafts = await DraftsRepo(uow.session, account_id).list_by_run(run.id)
        return self._view(run, drafts, spec.analysis, reused=True)

    async def _prepare(self, account_id: UUID, spec: _Spec, job_id: UUID | None) -> _Prepared:
        async with self._uow() as uow:
            session = uow.session
            pack = await build_pack(
                session,
                account_id,
                spec.task,
                embedder=self._embedder,
                now=self._clock.now(),
                knowledge=self._knowledge,
            )
            probation = None
            if spec.task.include_probation:
                probation = await self._probation.pick(session, account_id, spec.task)
            items = [
                k for k in pack.knowledge if probation is None or k.item_id != probation.item_id
            ]
            pack_id = None
            if items or probation is not None:
                pack_id = await self._recorder.record(
                    session, account_id, spec.task, items, probation
                )
            context = {
                **pack.to_ids().model_dump(mode="json"),
                **spec.extra,
                "job_id": str(job_id) if job_id else None,
                "hint": spec.hint,
                "probation": (
                    {"item_id": str(probation.item_id), "guidance": probation.guidance}
                    if probation is not None
                    else None
                ),
            }
            fields = {
                "kind": spec.kind,
                "topic_id": spec.topic_id,
                "context_pack": context,
                "passport_version": pack.passport_version,
                "prompt_id": await self._prompt_id(session, spec.prompt_key),
                "knowledge_pack_id": pack_id,
            }
            runs = RunsRepo(session, account_id)
            stale = await runs.by_job(job_id) if job_id is not None else None
            if stale is not None:
                run = await runs.update(
                    stale.id, status="running", error=None, finished_at=None, **fields
                )
            else:
                run = await runs.add(**fields)
            await emit(
                session,
                account_id=account_id,
                type=EventType.RUN_STARTED,
                entity=("generation_run", run.id),
                props={"kind": spec.kind},
            )
        return _Prepared(run.id, pack, render.pack_sections(pack), items, probation, pack_id)

    async def _prompt_id(self, session: DbSession, key: str) -> UUID | None:
        row = await PromptsRepo(session).find(key, self._prompts.get(key).content_hash)
        return row.id if row is not None else None

    # ── писатель ────────────────────────────────────────────────────────────────────────

    def _variables(
        self,
        spec: _Spec,
        prep: _Prepared,
        n: int,
        probation: KnowledgeItem | None,
        avoid: Sequence[str],
        *,
        retry_copy: bool = False,
    ) -> dict[str, Any]:
        s = prep.sections
        return {
            "passport": s["passport"],
            "task": render.render_task(spec.task),
            "hint": render.render_hint(spec.hint),
            "target": render.render_target(spec.target, spec.task.format),
            "original": render.render_original(spec.original) if spec.original else "",
            "analysis": render.render_analysis(spec.analysis or WhyItWorked()),
            "knowledge": render.render_knowledge(spec.task, prep.items, probation),
            "examples": s.get("examples", ""),
            "facts": s["facts"],
            "anti_examples": s.get("anti_examples", ""),
            "avoid": render.render_avoid(avoid, original=spec.original if retry_copy else None),
            "n": n,
            "format": spec.task.format,
        }

    async def _call(
        self,
        account_id: UUID,
        spec: _Spec,
        prep: _Prepared,
        n: int,
        probation: KnowledgeItem | None,
        avoid: Sequence[str] = (),
        *,
        retry_copy: bool = False,
    ) -> list[DraftOut]:
        batch = await self._gateway.generate(
            role=Role.WRITER,
            prompt_key=spec.prompt_key,
            variables=self._variables(spec, prep, n, probation, avoid, retry_copy=retry_copy),
            schema=DraftBatch,
            account_id=account_id,
            purpose=spec.purpose,
            entity=("generation_run", prep.run_id),
        )
        return batch.variants[:n]

    async def _redo(
        self,
        account_id: UUID,
        spec: _Spec,
        prep: _Prepared,
        variants: list[_Variant],
        indices: Sequence[int],
        *,
        retry_copy: bool = False,
    ) -> None:
        """Перегенерировать варианты по индексам: обычные — одним вызовом, 🧪 — своим."""
        groups: dict[bool, list[int]] = {}
        for i in indices:
            groups.setdefault(variants[i].probation is not None, []).append(i)
        for is_probe, idx in groups.items():
            probe = prep.probation if is_probe else None
            others = [v.out.text for j, v in enumerate(variants) if j not in idx]
            outs = await self._call(
                account_id, spec, prep, len(idx), probe, others, retry_copy=retry_copy
            )
            for i, out in zip(idx, outs, strict=False):
                variants[i] = _Variant(out, variants[i].probation)

    async def _write(
        self, account_id: UUID, spec: _Spec, prep: _Prepared, n: int
    ) -> tuple[list[_Variant], list[str]]:
        warnings: list[str] = list(prep.pack.warnings)
        probe = prep.probation if n > 1 else None
        regular = n - (1 if probe is not None else 0)
        variants = [_Variant(o) for o in await self._call(account_id, spec, prep, regular, None)]
        if probe is not None:
            written = [v.out.text for v in variants]
            outs = await self._call(account_id, spec, prep, 1, probe, written)
            variants += [_Variant(o, probe) for o in outs[:1]]
        if spec.original is not None:
            variants = await self._drop_copies(account_id, spec, prep, variants, warnings)
        redo = ensure_distinct([v.out.text for v in variants])
        if redo:
            await self._redo(account_id, spec, prep, variants, redo)
            if spec.original is not None:
                kept = [v for v in variants if not is_near_copy(v.out.text, spec.original)]
                if len(kept) < len(variants):
                    warnings.append("near_copy_dropped")
                variants = kept
            if ensure_distinct([v.out.text for v in variants]):
                warnings.append("variants_similar")
        if len(variants) < n:
            warnings.append("fewer_variants")
        return variants, sorted(set(warnings), key=warnings.index)

    async def _drop_copies(
        self,
        account_id: UUID,
        spec: _Spec,
        prep: _Prepared,
        variants: list[_Variant],
        warnings: list[str],
    ) -> list[_Variant]:
        original = spec.original or ""
        copies = [i for i, v in enumerate(variants) if is_near_copy(v.out.text, original)]
        if not copies:
            return variants
        await self._redo(account_id, spec, prep, variants, copies, retry_copy=True)
        kept = [v for v in variants if not is_near_copy(v.out.text, original)]
        if len(kept) < len(variants):
            warnings.append("near_copy_dropped")
        return kept

    # ── запись ──────────────────────────────────────────────────────────────────────────

    async def _save(
        self,
        account_id: UUID,
        spec: _Spec,
        prep: _Prepared,
        variants: list[_Variant],
        warnings: list[str],
    ) -> GenerationRun:
        fmt = spec.task.format
        target = spec.target
        views: list[DraftView] = []
        async with self._uow() as uow:
            session = uow.session
            drafts = DraftsRepo(session, account_id)
            apps = KnowledgeApplicationsRepo(session, account_id)
            for no, v in enumerate(variants, start=1):
                parts = list(v.out.parts)
                body = v.out.text if v.out.text.strip() else "\n\n".join(parts)
                saved_fmt = fmt
                if fmt == "thread":
                    parts = normalize_parts(parts) or split_post(body)
                elif fmt == "post" and weighted_len(body) > MAX_WEIGHTED_LEN:
                    # длинный пост режет код, не модель: несколько частей — тред
                    parts, saved_fmt = split_post(body), "thread"
                else:
                    parts = []
                issues = check_format(
                    FormatCheck(
                        format=saved_fmt,
                        text=body,
                        parts=parts,
                        reply_to_url=target.url if target else None,
                        has_target=target is not None,
                    )
                )
                row = await drafts.add(
                    run_id=prep.run_id,
                    variant_no=no,
                    format=saved_fmt,
                    text=body,
                    parts=parts,
                    reply_to_url=target.url if target else None,
                    probation_item_id=v.probation.item_id if v.probation else None,
                    lint_report={"format": issues} if issues else None,
                )
                await emit(
                    session,
                    account_id=account_id,
                    type=EventType.DRAFT_GENERATED,
                    entity=("draft", row.id),
                    props={"variant_no": no, "revision": 1, "format": fmt},
                )
                if prep.pack_id is not None:
                    for k in prep.items:
                        await apps.add(
                            pack_id=prep.pack_id,
                            item_id=k.item_id,
                            draft_id=row.id,
                            slot=render.application_slot(k.slot),
                        )
                    if v.probation is not None:
                        await apps.add(
                            pack_id=prep.pack_id,
                            item_id=v.probation.item_id,
                            draft_id=row.id,
                            slot="probation",
                        )
                views.append(
                    DraftView(
                        draft_id=row.id,
                        variant_no=no,
                        format=saved_fmt,
                        text=body,
                        parts=parts,
                        approach=v.out.approach,
                        probation_item_id=v.probation.item_id if v.probation else None,
                        probation_guidance=v.probation.guidance if v.probation else None,
                        issues=issues,
                    )
                )
            status = "done" if views else "failed"
            error = None if views else "no_variants"
            await RunsRepo(session, account_id).finish(
                prep.run_id, status=status, finished_at=self._clock.now(), error=error
            )
            await emit(
                session,
                account_id=account_id,
                type=EventType.RUN_FINISHED,
                entity=("generation_run", prep.run_id),
                props={"kind": spec.kind, "status": status, "drafts": len(views), "reason": error},
            )
        return GenerationRun(
            run_id=prep.run_id,
            kind=spec.kind,
            status=status,
            drafts=views,
            analysis=spec.analysis,
            warnings=warnings,
            error=error,
        )

    async def _fail(self, account_id: UUID, run_id: UUID, kind: str, reason: str) -> None:
        async with self._uow() as uow:
            await RunsRepo(uow.session, account_id).finish(
                run_id, status="failed", finished_at=self._clock.now(), error=reason
            )
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.RUN_FINISHED,
                entity=("generation_run", run_id),
                props={"kind": kind, "status": "failed", "drafts": 0, "reason": reason},
            )

    @staticmethod
    def _view(
        run: Any, drafts: Sequence[Any], analysis: WhyItWorked | None, *, reused: bool
    ) -> GenerationRun:
        probation = (run.context_pack or {}).get("probation") or {}
        views = [
            DraftView(
                draft_id=d.id,
                variant_no=d.variant_no,
                format=d.format,
                text=d.text,
                parts=list(d.parts or []),
                probation_item_id=d.probation_item_id,
                probation_guidance=probation.get("guidance") if d.probation_item_id else None,
                issues=list((d.lint_report or {}).get("format", [])),
            )
            for d in drafts
            if d.revision == 1
        ]
        return GenerationRun(
            run_id=run.id,
            kind=run.kind,
            status=run.status,
            drafts=views,
            analysis=analysis,
            reused=reused,
            error=run.error,
        )
