"""Контракт адаптеров: запрос к API и разбор ответа по записанным кассетам.

Кассеты собраны по документации провайдеров (ключей пока нет) — см. ADR 0007:
при live-проверке их нужно перезаписать настоящими ответами.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from xcopilot.llm.errors import PermanentProviderError, TransientProviderError
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.anthropic import AnthropicProvider
from xcopilot.llm.providers.base import Message, PromptBlock, TextRequest
from xcopilot.llm.providers.deepseek import DeepSeekProvider
from xcopilot.llm.providers.jev import JevProvider
from xcopilot.llm.schemas import ChoiceA, ChoiceQ, NoulA, NoulQ, Question, ScoreA, ScoreQ

CASSETTES = Path(__file__).resolve().parents[2] / "fixtures" / "cassettes"
KEY = "test-key-not-real"


class Recorder:
    """MockTransport, отдающий ответ кассеты и запоминающий запросы."""

    def __init__(self, name: str, *, status: int | None = None) -> None:
        self.cassette: dict[str, Any] = json.loads((CASSETTES / f"{name}.json").read_text())
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        resp = self.cassette["response"]
        if self.status is not None:
            return httpx2.Response(self.status, json={"error": {"message": "boom"}})
        return httpx2.Response(resp["status"], headers=resp["headers"], json=resp["body"])

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))

    def body(self, i: int = -1) -> Any:
        return json.loads(self.requests[i].content)


REQUEST = TextRequest(
    system=(
        PromptBlock("Пиши как автор.", cacheable=True),
        PromptBlock("<passport>\nКоротко.\n</passport>", cacheable=True),
        PromptBlock("Без хэштегов в середине.", cacheable=False),
    ),
    messages=(Message("user", "Напиши пост про кофе"),),
    max_tokens=512,
    json_mode=True,
)


async def test_contract_anthropic_cassette() -> None:
    rec = Recorder("anthropic_messages")
    result = await AnthropicProvider(KEY, http_client=rec.client()).complete(
        model="claude-opus-5-5", request=REQUEST
    )
    assert json.loads(result.text) == {"text": "Кофе — это ритуал.", "hashtags": ["coffee"]}
    assert result.model == "claude-opus-5-5"
    assert result.usage == Usage(
        input_tokens=1200, output_tokens=45, cache_read_tokens=4000, cache_write_tokens=900
    )
    [req] = rec.requests
    assert (req.method, req.url.path) == ("POST", rec.cassette["request"]["path"])
    assert req.headers["x-api-key"] == KEY
    body = rec.body()
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 512
    assert body["messages"] == [{"role": "user", "content": "Напиши пост про кофе"}]


async def test_cacheable_blocks_marked_in_request() -> None:
    rec = Recorder("anthropic_messages")
    await AnthropicProvider(KEY, http_client=rec.client()).complete(
        model="claude-opus-5-5", request=REQUEST
    )
    system = rec.body()["system"]
    assert [b["text"] for b in system] == [b.text for b in REQUEST.system]
    assert [b.get("cache_control") for b in system] == [
        {"type": "ephemeral"},
        {"type": "ephemeral"},
        None,
    ]


async def test_contract_deepseek_cassette() -> None:
    rec = Recorder("deepseek_chat")
    result = await DeepSeekProvider(KEY, http_client=rec.client()).complete(
        model="deepseek-flash", request=REQUEST
    )
    assert json.loads(result.text) == {"tags": ["кофе", "утро"]}
    assert result.usage == Usage(input_tokens=800, output_tokens=60)
    [req] = rec.requests
    assert str(req.url) == "https://api.deepseek.com/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = rec.body()
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "Пиши как автор." in body["messages"][0]["content"]
    assert "Без хэштегов" in body["messages"][0]["content"]
    assert body["messages"][1:] == [{"role": "user", "content": "Напиши пост про кофе"}]


async def test_contract_jev_cassette() -> None:
    rec = Recorder("jev_systemone")
    questions: dict[str, Question] = {
        "on_topic": NoulQ(instructions="По теме?"),
        "tone": ChoiceQ(
            instructions="Тон?", options={"angry": None, "calm": None, "excited": None}
        ),
        "hook": ScoreQ(
            instructions="Крючок?", levels=["Нет крючка", "Слабый", "Хороший", "Сильный"]
        ),
    }
    result = await JevProvider(KEY, http_client=rec.client()).decide(
        model="jev-latest", state="Пост про кофе", questions=questions
    )
    assert result.model == "jev-2026-10-01"
    assert result.usage == Usage(input_tokens=120, output_tokens=12)
    assert result.answers["on_topic"] == NoulA(p=0.98)
    assert isinstance(result.answers["tone"], ChoiceA)
    assert isinstance(result.answers["hook"], ScoreA)
    [req] = rec.requests
    assert (req.method, req.url.path) == ("POST", "/v1/systemone")
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = rec.body()
    assert body["model"] == "jev-latest" and body["state"] == "Пост про кофе"
    assert body["questions"]["hook"] == {
        "type": "score",
        "instructions": "Крючок?",
        "criteria": ["Нет крючка", "Слабый", "Хороший", "Сильный"],
    }


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, TransientProviderError), (500, TransientProviderError),
     (529, TransientProviderError), (400, PermanentProviderError),
     (401, PermanentProviderError)],
)  # fmt: skip
async def test_adapters_map_http_errors(status: int, error: type[Exception]) -> None:
    anthropic = Recorder("anthropic_messages", status=status)
    deepseek = Recorder("deepseek_chat", status=status)
    jev = Recorder("jev_systemone", status=status)
    with pytest.raises(error) as e1:
        await AnthropicProvider(KEY, http_client=anthropic.client()).complete(
            model="claude-opus-5-5", request=REQUEST
        )
    with pytest.raises(error) as e2:
        await DeepSeekProvider(KEY, http_client=deepseek.client()).complete(
            model="deepseek-flash", request=REQUEST
        )
    with pytest.raises(error) as e3:
        await JevProvider(KEY, http_client=jev.client()).decide(
            model="jev-latest", state="x", questions={"q": NoulQ(instructions="?")}
        )
    for exc in (e1.value, e2.value, e3.value):
        assert getattr(exc, "status", None) == status
    # повторы — дело шлюза: SDK сам не повторяет
    assert len(anthropic.requests) == len(deepseek.requests) == len(jev.requests) == 1


async def test_adapters_map_timeouts_to_transient() -> None:
    def boom(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timeout", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(boom))
    with pytest.raises(TransientProviderError):
        await AnthropicProvider(KEY, http_client=client).complete(
            model="claude-opus-5-5", request=REQUEST
        )
    with pytest.raises(TransientProviderError):
        await DeepSeekProvider(KEY, http_client=client).complete(
            model="deepseek-flash", request=REQUEST
        )
    with pytest.raises(TransientProviderError):
        await JevProvider(KEY, http_client=client).decide(
            model="jev-latest", state="x", questions={"q": NoulQ(instructions="?")}
        )
