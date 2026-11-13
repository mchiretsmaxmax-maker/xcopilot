"""Адаптер Jev (TypeSafe System One, typesafe-sdk): вопросы судьи → ответы с вероятностями.

`POST /v1/systemone`: state + именованные вопросы (noul / choice / score) → ответы. Выход
бесплатный, платится только вход. SDK сам не повторяет: повторы — в шлюзе.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx2
from typesafe_sdk import (
    AsyncTypeSafeClient,
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeError,
)

from xcopilot.llm.errors import (
    PermanentProviderError,
    ProviderError,
    TransientProviderError,
    status_error,
)
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.base import DecisionResult
from xcopilot.llm.schemas import Answer, ChoiceA, ChoiceQ, NoulA, NoulQ, Question, ScoreA

NAME = "jev"


def to_jev_question(q: Question) -> dict[str, Any]:
    """Наш вопрос → вопрос System One (score: criteria по порядку = уровни 0..N-1)."""
    if isinstance(q, NoulQ):
        out: dict[str, Any] = {"type": "noul", "instructions": q.instructions}
        criteria = {k: v for k, v in (("true", q.yes), ("false", q.no)) if v is not None}
        if criteria:
            out["criteria"] = criteria
        return out
    if isinstance(q, ChoiceQ):
        return {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
    return {"type": "score", "instructions": q.instructions, "criteria": list(q.levels)}


def _answer(raw: Mapping[str, Any]) -> Answer | None:
    kind = raw.get("type")
    if kind == "noul":
        return NoulA(p=float(raw["noul"]))
    if kind == "choice":
        return ChoiceA(
            choice=str(raw["choice"]),
            confidence=float(raw["confidence"]),
            probabilities={str(k): float(v) for k, v in raw["probabilities"].items()},
        )
    if kind == "score":
        return ScoreA(
            score=float(raw["score"]),
            confidence=float(raw["confidence"]),
            probabilities={int(k): float(v) for k, v in raw["probabilities"].items()},
        )
    return None  # тип ответа новее нашего кода (например, box) — пропускаем


def parse_system_one(data: Mapping[str, Any]) -> DecisionResult:
    """Ответ System One (JSON) → DecisionResult."""
    answers: dict[str, Answer] = {}
    for name, raw in data.get("answers", {}).items():
        answer = _answer(raw)
        if answer is not None:
            answers[name] = answer
    usage = data.get("usage") or {}
    return DecisionResult(
        answers=answers,
        usage=Usage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        ),
        model=str(data["model"]),
    )


class JevProvider:
    name = NAME

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx2.AsyncClient | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client = AsyncTypeSafeClient(
            api_key=api_key,
            base_url=base_url,
            retry=RetryPolicy(max_retries=0),
            timeout=timeout,
            http_client=http_client,
            compression=False,
        )

    async def decide(
        self, *, model: str, state: str, questions: Mapping[str, Question]
    ) -> DecisionResult:
        payload = {name: to_jev_question(q) for name, q in questions.items()}
        error: ProviderError | None = None
        try:
            resp = await self._client.system_one(
                state=state,
                questions=payload,  # type: ignore[arg-type]
                model=model,
            )
        except TypeSafeAPIError as exc:
            error = status_error(self.name, exc.status)
        except TypeSafeAPIConnectionError as exc:
            error = TransientProviderError(self.name, kind=type(exc).__name__)
        except TypeSafeError as exc:
            error = PermanentProviderError(self.name, kind=type(exc).__name__)
        if error is not None:
            raise error  # вне except: без __context__ с телом ответа
        return parse_system_one(resp.model_dump(mode="json"))
