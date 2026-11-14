"""LLMGateway: вызов модели по роли с проверкой ответа, журналом, бюджетом и заменами.

Порядок каждого вызова провайдера: оценка цены → `BudgetGuard.check` (жёсткий лимит —
`BudgetExceeded`, провайдер не вызывается) → предохранитель → вызов с повторами на временных
сбоях → строка `llm_calls` (токены, кэш, цена, задержка, статус, сущность, версия промпта)
→ `BudgetGuard.record`.

- `generate`: текст по шаблону промпта → JSON по pydantic-схеме; невалидный ответ —
  одна починка с текстом ошибки, вторая неудача — `LLMOutputError` и событие `llm.failed`.
- `decide`: вопросы судье (Jev, либо текстовая модель вроде DeepSeek: тогда тот же путь, что у
  резервного, но `fallback=False`). Если судья недоступен — те же вопросы резервной модели
  (`[fallback]` в models.toml) с флагом `fallback`. У writer замены нет.

Ключи в журнал, события и тексты ошибок не попадают: в журнал пишется только класс ошибки
и HTTP-код, адаптеры не прикладывают тела ответов.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.clock import Clock
from xcopilot.db.repos import LlmCallsRepo, PromptsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import ConfigError
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.errors import (
    LLMOutputError,
    PermanentProviderError,
    ProviderError,
    ProviderUnavailable,
    TransientProviderError,
)
from xcopilot.llm.fallback_judge import PROMPT_KEY as FALLBACK_PROMPT
from xcopilot.llm.fallback_judge import extract_json, parse_answers, prompt_variables
from xcopilot.llm.models_config import ModelsConfig, RoleConfig
from xcopilot.llm.pricing import PricingTable, Usage, price
from xcopilot.llm.prompts.registry import PromptRegistry, RenderedPrompt
from xcopilot.llm.providers.base import (
    DecisionProvider,
    DecisionResult,
    Image,
    Message,
    ProviderResult,
    TextProvider,
    TextRequest,
)
from xcopilot.llm.resilience import CircuitBreaker, RetryPolicy, Sleep, call_with_retry
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import Answer, Question, Verdict
from xcopilot.metrics.events import EventType, emit

SCOPE = "llm"
IMAGE_TOKENS = 800  # оценка картинки для бюджета до вызова (фактические токены — из ответа)
CHARS_PER_TOKEN = 3  # грубая оценка для бюджета до вызова (с запасом для кириллицы)


@dataclass(frozen=True)
class CallContext:
    account_id: UUID
    purpose: str
    entity: tuple[str, UUID] | None
    prompt: RenderedPrompt | None = None


def schema_instruction(schema: type[BaseModel]) -> str:
    spec = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return f"\n\nОтветь только JSON-объектом по этой JSON-схеме, без пояснений:\n{spec}"


def repair_instruction(error: Exception) -> str:
    return (
        f"Ответ не прошёл проверку: {error}\n"
        "Пришли исправленный ответ целиком — только JSON по той же схеме."
    )


def _bind[R](fn: Callable[..., Awaitable[R]], **kwargs: Any) -> Callable[[], Awaitable[R]]:
    """Вызов без аргументов с зафиксированными kwargs (для повторов)."""
    return lambda: fn(**kwargs)


def estimate_tokens(*texts: str) -> int:
    return sum(len(t) for t in texts) // CHARS_PER_TOKEN + 1


class LLMGateway:
    def __init__(
        self,
        *,
        models: ModelsConfig,
        pricing: PricingTable,
        providers: Mapping[str, TextProvider | DecisionProvider],
        prompts: PromptRegistry,
        budget: BudgetGuard,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        retry: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
        breaker_threshold: int = 5,
        breaker_reset_after: float = 30.0,
    ) -> None:
        self._models = models
        self._pricing = pricing
        self._providers = dict(providers)
        self._prompts = prompts
        self._budget = budget
        self._uow = uow_factory
        self._clock = clock
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._breakers = {
            name: CircuitBreaker(
                name, clock, failure_threshold=breaker_threshold, reset_after=breaker_reset_after
            )
            for name in self._providers
        }

    # ── публичный API ───────────────────────────────────────────────────────────────────

    async def generate[T: BaseModel](
        self,
        *,
        role: Role,
        prompt_key: str,
        variables: Mapping[str, Any],
        schema: type[T],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
        images: Sequence[Image] = (),
    ) -> T:
        """Ответ модели роли по шаблону промпта, проверенный pydantic-схемой.

        `images` — картинки к сообщению (vision, ТЗ v3.1): провайдер без vision отказывает.
        """
        rc = self._models.roles[role]
        rendered = self._prompts.render(prompt_key, variables)
        request = TextRequest(
            system=rendered.system,
            messages=(
                Message("user", rendered.user + schema_instruction(schema), images=tuple(images)),
            ),
            max_tokens=rc.max_tokens,
            json_mode=True,
        )
        ctx = CallContext(account_id, purpose, entity, rendered)
        try:
            value, _ = await self._text_with_repair(
                role, rc, request, ctx, lambda text: schema.model_validate_json(extract_json(text))
            )
        except (LLMOutputError, ProviderUnavailable, PermanentProviderError) as exc:
            await self._failed(account_id, role, rc.provider, exc)
            raise
        return value

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict:
        """Ответы судьи на вопросы о тексте; при недоступном судье — резервная модель."""
        if not questions:
            raise ValueError("decide: нужен хотя бы один вопрос")
        rc = self._models.roles[Role.JUDGE]
        ctx = CallContext(account_id, purpose, entity)
        if not hasattr(self._providers.get(rc.provider), "decide"):
            # судья — текстовая модель (например DeepSeek): те же вопросы и схема Verdict, что у
            # резервного судьи, но это основной судья, `fallback=False`
            return await self._text_decide(rc, state, questions, ctx, fallback=False)
        provider = self._decision_provider(rc)
        questions_json = json.dumps({k: q.model_dump() for k, q in questions.items()})
        estimate = self._estimate(rc, Usage(input_tokens=estimate_tokens(state, questions_json)))
        await self._budget.check(account_id, SCOPE, estimate)
        try:
            result = await self._invoke(
                Role.JUDGE,
                rc,
                ctx,
                lambda: provider.decide(model=rc.model, state=state, questions=questions),
            )
        except ProviderUnavailable as exc:
            await self._failed(account_id, Role.JUDGE, rc.provider, exc)
            return await self._fallback_decide(state, questions, ctx)
        except PermanentProviderError as exc:
            await self._failed(account_id, Role.JUDGE, rc.provider, exc)
            raise
        if missing := sorted(set(questions) - result.answers.keys()):
            incomplete = LLMOutputError(f"{rc.provider}: нет ответов на {', '.join(missing)}")
            await self._failed(account_id, Role.JUDGE, rc.provider, incomplete)
            raise incomplete
        answers = {name: result.answers[name] for name in questions}
        return Verdict(answers=answers, judge=result.model, fallback=False)

    # ── внутреннее ──────────────────────────────────────────────────────────────────────

    async def _fallback_decide(
        self, state: str, questions: Mapping[str, Question], ctx: CallContext
    ) -> Verdict:
        target = self._models.fallback.get(Role.JUDGE)
        if target is None:
            raise ProviderUnavailable("судья недоступен, резервный судья не настроен")
        return await self._text_decide(
            self._models.roles[target], state, questions, ctx, fallback=True
        )

    async def _text_decide(
        self,
        rc: RoleConfig,
        state: str,
        questions: Mapping[str, Question],
        ctx: CallContext,
        *,
        fallback: bool,
    ) -> Verdict:
        rendered = self._prompts.render(FALLBACK_PROMPT, prompt_variables(state, questions))
        request = TextRequest(
            system=rendered.system,
            messages=(Message("user", rendered.user),),
            max_tokens=rc.max_tokens,
            json_mode=True,
        )

        def parse(text: str) -> dict[str, Answer]:
            return parse_answers(text, questions)

        try:
            answers, result = await self._text_with_repair(
                Role.JUDGE, rc, request, replace(ctx, prompt=rendered), parse
            )
        except (ProviderUnavailable, PermanentProviderError, LLMOutputError) as exc:
            await self._failed(ctx.account_id, Role.JUDGE, rc.provider, exc)
            if not fallback:
                raise ProviderUnavailable(f"{rc.provider}: судья недоступен") from None
            raise ProviderUnavailable("судья и резервный судья недоступны") from None
        return Verdict(answers=answers, judge=result.model, fallback=fallback)

    async def _text_with_repair[V](
        self,
        role: Role,
        rc: RoleConfig,
        request: TextRequest,
        ctx: CallContext,
        parse: Callable[[str], V],
    ) -> tuple[V, ProviderResult]:
        """Вызов + проверка; невалидный ответ — одна починка с текстом ошибки."""
        provider = self._text_provider(rc)
        for attempt in (1, 2):
            estimate = self._estimate(
                rc,
                Usage(
                    input_tokens=estimate_tokens(
                        *(b.text for b in request.system), *(m.content for m in request.messages)
                    )
                    + IMAGE_TOKENS * sum(len(m.images) for m in request.messages),
                    output_tokens=request.max_tokens,
                ),
            )
            await self._budget.check(ctx.account_id, SCOPE, estimate)
            result = await self._invoke(
                role, rc, ctx, _bind(provider.complete, model=rc.model, request=request)
            )
            try:
                return parse(result.text), result
            except (ValueError, TypeError, KeyError) as exc:
                if attempt == 2:
                    break
                request = replace(
                    request,
                    messages=(
                        *request.messages,
                        Message("assistant", result.text),
                        Message("user", repair_instruction(exc)),
                    ),
                )
        raise LLMOutputError(f"{role.value}: ответ {rc.provider} не прошёл проверку дважды")

    async def _invoke[R: (ProviderResult, DecisionResult)](
        self,
        role: Role,
        rc: RoleConfig,
        ctx: CallContext,
        call: Callable[[], Awaitable[R]],
    ) -> R:
        """Предохранитель → повторы → журнал → бюджет. Временный сбой → ProviderUnavailable."""
        breaker = self._breakers[rc.provider]
        breaker.before_call()
        started = self._clock.now()
        error: ProviderError | None = None
        try:
            result = await call_with_retry(call, self._retry, sleep=self._sleep)
        except ProviderError as exc:
            error = exc
        if error is not None:
            if isinstance(error, TransientProviderError):
                breaker.on_failure()
            await self._log(role, rc, ctx, started, model=rc.model, error=error)
            if isinstance(error, TransientProviderError):
                raise ProviderUnavailable(f"{rc.provider}: недоступен ({error.short()})")
            raise error
        breaker.on_success()
        cost = price(
            rc.provider, result.model, result.usage, self._clock.now(), table=self._pricing
        )
        await self._log(role, rc, ctx, started, model=result.model, usage=result.usage, cost=cost)
        await self._budget.record(ctx.account_id, SCOPE, cost)
        return result

    async def _log(
        self,
        role: Role,
        rc: RoleConfig,
        ctx: CallContext,
        started: Any,
        *,
        model: str,
        usage: Usage | None = None,
        cost: int = 0,
        error: ProviderError | None = None,
    ) -> None:
        usage = usage or Usage()
        now = self._clock.now()
        async with self._uow() as uow:
            prompt_id = None
            if ctx.prompt is not None:
                row = await PromptsRepo(uow.session).find(ctx.prompt.key, ctx.prompt.content_hash)
                prompt_id = row.id if row is not None else None
            entity_type, entity_id = ctx.entity if ctx.entity is not None else (None, None)
            await LlmCallsRepo(uow.session, ctx.account_id).log(
                ts=now,
                role=role.value,
                provider=rc.provider,
                model=model,
                prompt_id=prompt_id,
                purpose=ctx.purpose,
                input_tokens=usage.input_tokens,
                cached_tokens=usage.cache_read_tokens,
                output_tokens=usage.output_tokens,
                cost_micro_usd=cost,
                latency_ms=int((now - started).total_seconds() * 1000),
                status="error" if error is not None else "ok",
                error=error.short() if error is not None else None,
                entity_type=entity_type,
                entity_id=entity_id,
            )

    async def _failed(self, account_id: UUID, role: Role, provider: str, exc: Exception) -> None:
        async with self._uow() as uow:
            await emit(
                uow.session,
                account_id=account_id,
                type=EventType.LLM_FAILED,
                props={"role": role.value, "provider": provider, "error": type(exc).__name__},
                ts=self._clock.now(),
            )

    def _estimate(self, rc: RoleConfig, usage: Usage) -> int:
        return price(rc.provider, rc.model, usage, self._clock.now(), table=self._pricing)

    def _text_provider(self, rc: RoleConfig) -> TextProvider:
        provider = self._providers.get(rc.provider)
        if provider is None or not hasattr(provider, "complete"):
            raise ConfigError(f"провайдер {rc.provider} не умеет генерировать текст")
        return provider  # type: ignore[return-value]

    def _decision_provider(self, rc: RoleConfig) -> DecisionProvider:
        provider = self._providers.get(rc.provider)
        if provider is None or not hasattr(provider, "decide"):
            raise ConfigError(f"провайдер {rc.provider} не умеет отвечать на вопросы судьи")
        return provider  # type: ignore[return-value]
