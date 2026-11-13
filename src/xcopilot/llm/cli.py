"""`python -m xcopilot.llm ping`: ответ каждой роли, токены и стоимость."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable, Usage
from xcopilot.llm.providers.base import DecisionProvider, TextProvider
from xcopilot.llm.roles import Role


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
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> int:
    raise NotImplementedError
