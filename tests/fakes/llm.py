"""FakeLLM и FakeJudge: подделки провайдеров моделей для тестов шлюза.

Ответы задаются сценарием: строка (текст ответа), `ProviderResult` / `DecisionResult`
или исключение.
Каждый вызов запоминается в `calls`. `latency` сдвигает FakeClock — так проверяется задержка.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tests.fakes.clock import FakeClock
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.base import DecisionResult, ProviderResult, TextRequest
from xcopilot.llm.schemas import Answer, NoulA, Question

DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)


@dataclass
class TextCall:
    model: str
    request: TextRequest


@dataclass
class DecideCall:
    model: str
    state: str
    questions: dict[str, Question]


@dataclass
class FakeLLM:
    """Текстовый провайдер по сценарию."""

    name: str = "anthropic"
    script: list[str | ProviderResult | Exception] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: DEFAULT_USAGE)
    clock: FakeClock | None = None
    latency: float = 0.0
    calls: list[TextCall] = field(default_factory=list)

    def push(self, *items: str | ProviderResult | Exception) -> FakeLLM:
        self.script.extend(items)
        return self

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        self.calls.append(TextCall(model=model, request=request))
        if self.clock is not None and self.latency:
            self.clock.advance(self.latency)
        if not self.script:
            raise AssertionError(f"FakeLLM({self.name}): сценарий закончился")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ProviderResult):
            return item
        return ProviderResult(text=item, usage=self.usage, model=model)


@dataclass
class FakeJudge:
    """Провайдер решений (судья) по сценарию."""

    name: str = "jev"
    script: list[Mapping[str, Answer] | DecisionResult | Exception] = field(default_factory=list)
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=500, output_tokens=10))
    calls: list[DecideCall] = field(default_factory=list)

    def push(self, *items: Mapping[str, Answer] | DecisionResult | Exception) -> FakeJudge:
        self.script.extend(items)
        return self

    async def decide(
        self, *, model: str, state: str, questions: Mapping[str, Question]
    ) -> DecisionResult:
        self.calls.append(DecideCall(model=model, state=state, questions=dict(questions)))
        if not self.script:
            raise AssertionError(f"FakeJudge({self.name}): сценарий закончился")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, DecisionResult):
            return item
        return DecisionResult(answers=dict(item), usage=self.usage, model=model)


def yes_answers(names: Sequence[str], p: float = 0.9) -> dict[str, Answer]:
    """Ответы «да» с вероятностью p на все вопросы-noul."""
    return {name: NoulA(p=p) for name in names}
