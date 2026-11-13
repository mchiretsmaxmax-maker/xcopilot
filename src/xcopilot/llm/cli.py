"""`python -m xcopilot.llm ping`: ответ каждой роли, токены и стоимость."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

from xcopilot.llm.errors import ProviderError, ProviderUnavailable
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable, Usage, price
from xcopilot.llm.providers.base import (
    DecisionProvider,
    Message,
    TextProvider,
    TextRequest,
)
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import NoulA, NoulQ


@dataclass(frozen=True)
class PingResult:
    role: Role
    provider: str
    model: str
    ok: bool
    answer: str = ""
    usage: Usage = field(default_factory=Usage)
    cost_micro_usd: int = 0
    error: str = ""


async def ping(
    providers: Mapping[str, TextProvider | DecisionProvider],
    models: ModelsConfig,
    pricing: PricingTable,
    *,
    out: Callable[[str], object] = print,
) -> list[PingResult]:
    results: list[PingResult] = []
    for role in list(Role):
        rc = models.roles[role]
        provider = providers.get(rc.provider)
        if provider is None:
            result = PingResult(role, rc.provider, rc.model, ok=False, error="нет ключа")
            out(f"{role.value:<7} {rc.provider}/{rc.model}  ОШИБКА {result.error}")
            results.append(result)
            continue

        try:
            if role is Role.JUDGE:
                decision = await cast(DecisionProvider, provider).decide(
                    model=rc.model,
                    state="pong",
                    questions={"pong": NoulQ(instructions="Это слово pong?")},
                )
                first = next(iter(decision.answers.values()))
                if isinstance(first, NoulA):
                    answer = f"pong: p={first.p:.2f}"
                else:
                    answer = f"pong: {first.kind}"
                usage = decision.usage
                model = decision.model
            else:
                text_result = await cast(TextProvider, provider).complete(
                    model=rc.model,
                    request=TextRequest(
                        system=(),
                        messages=(Message("user", "Ответь одним словом: pong"),),
                        max_tokens=16,
                    ),
                )
                answer = text_result.text.strip()[:80]
                usage = text_result.usage
                model = text_result.model

            cost = price(rc.provider, model, usage, datetime.now(UTC), table=pricing)
            result = PingResult(
                role,
                rc.provider,
                model,
                ok=True,
                answer=answer,
                usage=usage,
                cost_micro_usd=cost,
            )
            out(
                f"{role.value:<7} {rc.provider}/{model}  "
                f"in={usage.input_tokens} out={usage.output_tokens}  "
                f"${cost / 1_000_000:.6f}  → {answer}"
            )
        except ProviderError as exc:
            result = PingResult(
                role,
                rc.provider,
                rc.model,
                ok=False,
                error=exc.short(),
            )
            out(f"{role.value:<7} {rc.provider}/{rc.model}  ОШИБКА {result.error}")
        except ProviderUnavailable as exc:
            result = PingResult(
                role,
                rc.provider,
                rc.model,
                ok=False,
                error=type(exc).__name__,
            )
            out(f"{role.value:<7} {rc.provider}/{rc.model}  ОШИБКА {result.error}")
        results.append(result)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["ping"]:
        print("использование: python -m xcopilot.llm ping", file=sys.stderr)
        return 2

    from xcopilot.config import load_settings
    from xcopilot.llm.factory import build_providers

    settings = load_settings()
    results = asyncio.run(ping(build_providers(settings), ModelsConfig.load(), PricingTable.load()))
    return 0 if all(r.ok for r in results) else 1
