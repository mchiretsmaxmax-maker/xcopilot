"""LLMGateway.generate / decide: схема, починка, журнал, замены, сбои, ключи, конфиг ролей."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from pydantic import BaseModel

from tests.fakes.llm import yes_answers
from tests.integration.llm.conftest import Rig
from xcopilot.db.repos import LlmCallsRepo
from xcopilot.errors import BudgetExceeded
from xcopilot.llm.errors import (
    LLMOutputError,
    PermanentProviderError,
    ProviderUnavailable,
    TransientProviderError,
)
from xcopilot.llm.models_config import DEFAULT_MODELS_PATH, ModelsConfig
from xcopilot.llm.pricing import Usage, price
from xcopilot.llm.providers.anthropic import AnthropicProvider
from xcopilot.llm.providers.base import ProviderResult
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import (
    Answer,
    ChoiceA,
    ChoiceQ,
    NoulA,
    NoulQ,
    Question,
    ScoreA,
    ScoreQ,
    Verdict,
)


class Post(BaseModel):
    text: str
    hashtags: list[str]


VARS = {"topic": "кофе", "passport": "Коротко и по делу."}
GOOD = '{"text": "Пост про кофе", "hashtags": ["coffee"]}'
QUESTIONS: dict[str, Question] = {
    "on_topic": NoulQ(instructions="Пост по теме?"),
    "tone": ChoiceQ(instructions="Тон?", options={"calm": "Спокойный", "angry": "Злой"}),
    "hook": ScoreQ(instructions="Сила крючка?", levels=["нет", "слабый", "хороший", "сильный"]),
}


def unavailable(provider: str, n: int = 4) -> list[Exception]:
    """Ошибки на первый вызов и все 3 повтора."""
    return [TransientProviderError(provider, status=503) for _ in range(n)]


async def gen(rig: Rig, **kw: object) -> Post:
    args: dict[str, object] = {
        "role": Role.WRITER,
        "prompt_key": "test.post",
        "variables": VARS,
        "schema": Post,
        "account_id": rig.account_id,
        "purpose": "draft.generate",
    }
    args.update(kw)
    return await rig.gateway().generate(**args)  # type: ignore[arg-type]


async def test_generate_returns_validated_schema(rig: Rig) -> None:
    rig.writer.push(GOOD)
    post = await gen(rig)
    assert post == Post(text="Пост про кофе", hashtags=["coffee"])
    [call] = rig.writer.calls
    assert call.model == "claude-opus-5-5"
    assert call.request.json_mode is True
    assert '"hashtags"' in call.request.messages[-1].content  # JSON-схема ответа в запросе
    assert "кофе" in call.request.messages[0].content


async def test_generate_repairs_invalid_output_once(rig: Rig) -> None:
    rig.writer.push('{"text": "без тегов"}', f"```json\n{GOOD}\n```")
    post = await gen(rig)
    assert post.hashtags == ["coffee"]
    _first, second = rig.writer.calls
    msgs = second.request.messages
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1].content == '{"text": "без тегов"}'
    assert "hashtags" in msgs[2].content  # текст ошибки проверки передан модели
    rows = await rig.rows("SELECT status FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    assert [r.status for r in rows] == ["ok", "ok"]


async def test_generate_fails_after_second_invalid_output(rig: Rig) -> None:
    rig.writer.push("не JSON", '{"text": 1}')
    with pytest.raises(LLMOutputError):
        await gen(rig)
    assert len(rig.writer.calls) == 2
    [ev] = await rig.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = 'llm.failed'", a=rig.account_id
    )
    assert ev.props == {"role": "writer", "provider": "anthropic", "error": "LLMOutputError"}


async def test_call_logged_with_tokens_cost_latency_entity(rig: Rig) -> None:
    async with rig.uow() as uow:
        synced = await rig.prompts.sync(uow.session)
    usage = Usage(
        input_tokens=1200, output_tokens=300, cache_read_tokens=4000, cache_write_tokens=900
    )
    rig.writer.latency = 0.25
    rig.writer.push(ProviderResult(text=GOOD, usage=usage, model="claude-opus-5-5"))
    draft_id = uuid4()
    await gen(rig, entity=("draft", draft_id))
    [row] = await rig.rows(
        "SELECT * FROM llm_calls WHERE account_id = :a AND entity_id = :e",
        a=rig.account_id,
        e=draft_id,
    )
    assert (row.role, row.provider, row.model, row.purpose) == (
        "writer",
        "anthropic",
        "claude-opus-5-5",
        "draft.generate",
    )
    assert (row.input_tokens, row.output_tokens, row.cached_tokens) == (1200, 300, 4000)
    assert row.cost_micro_usd == price("anthropic", "claude-opus-5-5", usage, rig.clock.now())
    assert row.latency_ms == 250
    assert (row.status, row.error) == ("ok", None)
    assert row.entity_type == "draft"
    assert row.prompt_id == synced["test.post"].id
    assert row.ts == rig.clock.now()
    async with rig.uow() as uow:
        repo = LlmCallsRepo(uow.session, rig.account_id)
        assert [c.id for c in await repo.for_entity("draft", draft_id)] == [row.id]


async def test_budget_hard_cap_blocks_before_request(rig: Rig) -> None:
    from xcopilot.db.repos import BudgetsRepo

    async with rig.uow() as uow:
        await BudgetsRepo(uow.session, rig.account_id).upsert("llm", "day", limit_micro_usd=10_000)
        await LlmCallsRepo(uow.session, rig.account_id).log(
            ts=rig.clock.now(), role="writer", provider="anthropic", model="claude-opus-5-5",
            purpose="seed", cost_micro_usd=9_990, status="ok",
        )  # fmt: skip
    rig.writer.push(GOOD)
    with pytest.raises(BudgetExceeded):
        await gen(rig)
    assert rig.writer.calls == []  # провайдер не вызван
    [ev] = await rig.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = 'budget.exceeded'",
        a=rig.account_id,
    )
    assert ev.props["scope"] == "llm" and ev.props["limit_micro_usd"] == 10_000


async def test_writer_has_no_fallback(rig: Rig) -> None:
    rig.writer.push(*unavailable("anthropic"))
    with pytest.raises(ProviderUnavailable):
        await gen(rig)
    assert len(rig.writer.calls) == 4  # первый вызов + 3 повтора
    assert rig.cheap.calls == []  # DeepSeek не пишет текст за Claude
    rows = await rig.rows(
        "SELECT status, error FROM llm_calls WHERE account_id = :a", a=rig.account_id
    )
    assert [(r.status, r.error) for r in rows] == [("error", "TransientProviderError 503")]
    [ev] = await rig.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = 'llm.failed'", a=rig.account_id
    )
    assert ev.props["role"] == "writer"


async def test_cheap_role_permanent_error_not_retried(rig: Rig) -> None:
    rig.cheap.push(PermanentProviderError("deepseek", status=400))
    with pytest.raises(PermanentProviderError):
        await gen(rig, role=Role.CHEAP)
    assert len(rig.cheap.calls) == 1


async def test_decide_returns_verdict_from_jev(rig: Rig) -> None:
    answers: dict[str, Answer] = {
        "on_topic": NoulA(p=0.9),
        "tone": ChoiceA(choice="calm", confidence=0.8, probabilities={"calm": 0.8, "angry": 0.2}),
        "hook": ScoreA(score=2.1, confidence=0.7, probabilities={0: 0.1, 1: 0.1, 2: 0.4, 3: 0.4}),
    }
    rig.judge.push(answers)
    verdict = await rig.gateway().decide(
        state="Пост про кофе", questions=QUESTIONS, account_id=rig.account_id, purpose="draft.judge"
    )
    assert verdict == Verdict(answers=answers, judge="jev-latest", fallback=False)
    [row] = await rig.rows("SELECT * FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    assert (row.role, row.provider, row.output_tokens) == ("judge", "jev", 10)
    assert row.cost_micro_usd == price("jev", "jev-latest", rig.judge.usage, rig.clock.now())


FALLBACK_OK = json.dumps(
    {
        "answers": {
            "on_topic": {"p": 0.7},
            "tone": {"choice": "calm", "confidence": 0.6},
            "hook": {"score": 2, "confidence": 0.5},
        }
    }
)


async def test_judge_falls_back_to_cheap_with_same_questions_and_flag(rig: Rig) -> None:
    rig.judge.push(*unavailable("jev"))
    rig.cheap.push(FALLBACK_OK)
    verdict = await rig.gateway().decide(
        state="Пост про кофе", questions=QUESTIONS, account_id=rig.account_id, purpose="draft.judge"
    )
    assert verdict.fallback is True
    assert verdict.judge == "deepseek-flash"
    assert set(verdict.answers) == set(QUESTIONS)
    [call] = rig.cheap.calls
    assert call.model == "deepseek-flash"
    assert call.request.json_mode is True
    body = "\n".join(
        [b.text for b in call.request.system] + [m.content for m in call.request.messages]
    )
    for name, q in QUESTIONS.items():
        assert name in body and q.instructions in body
    assert "сильный" in body and "Спокойный" in body
    assert '<untrusted label="state">' in body  # проверяемый текст обёрнут как данные
    roles = await rig.rows(
        "SELECT role, provider, status FROM llm_calls WHERE account_id = :a ORDER BY ts, id",
        a=rig.account_id,
    )
    assert [(r.role, r.provider, r.status) for r in roles] == [
        ("judge", "jev", "error"),
        ("judge", "deepseek", "ok"),
    ]


async def test_judge_unavailable_and_fallback_unavailable_raises(rig: Rig) -> None:
    rig.judge.push(*unavailable("jev"))
    rig.cheap.push(*unavailable("deepseek"))
    with pytest.raises(ProviderUnavailable):
        await rig.gateway().decide(
            state="Пост", questions=QUESTIONS, account_id=rig.account_id, purpose="draft.judge"
        )
    events = await rig.rows(
        "SELECT props FROM events WHERE account_id = :a AND type = 'llm.failed'", a=rig.account_id
    )
    assert {e.props["provider"] for e in events} >= {"jev", "deepseek"}


async def test_fallback_judge_output_matches_verdict_schema(rig: Rig) -> None:
    rig.judge.push(*unavailable("jev"))
    # первый ответ резерва: вариант вне списка и уровень вне шкалы → одна починка
    bad = {"answers": {"on_topic": {"p": 0.7}, "tone": {"choice": "happy", "confidence": 0.5},
                       "hook": {"score": 7, "confidence": 0.5}}}  # fmt: skip
    rig.cheap.push(json.dumps(bad), FALLBACK_OK)
    verdict = await rig.gateway().decide(
        state="Пост", questions=QUESTIONS, account_id=rig.account_id, purpose="draft.judge"
    )
    assert len(rig.cheap.calls) == 2
    assert Verdict.model_validate_json(verdict.model_dump_json()) == verdict
    on_topic, tone, hook = (verdict.answers[k] for k in ("on_topic", "tone", "hook"))
    assert isinstance(on_topic, NoulA) and on_topic.p == 0.7
    assert isinstance(tone, ChoiceA) and tone.choice == "calm"
    assert set(tone.probabilities) == {"calm", "angry"}
    assert abs(sum(tone.probabilities.values()) - 1) < 1e-9
    assert isinstance(hook, ScoreA) and hook.score == 2
    assert set(hook.probabilities) == {0, 1, 2, 3}
    assert abs(sum(hook.probabilities.values()) - 1) < 1e-9


async def test_api_keys_never_in_call_log_or_errors(rig: Rig) -> None:
    secret = "sk-ant-TESTSECRET-0123456789"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["x-api-key"] == secret
        status = 401 if "first" not in handler.__dict__ else 500
        handler.__dict__["first"] = True
        return httpx2.Response(
            status,
            json={"type": "error", "error": {"type": "authentication_error",
                                             "message": f"invalid x-api-key: {secret}"}},
        )  # fmt: skip

    provider = AnthropicProvider(
        secret, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    gw = rig.gateway(providers={"anthropic": provider, "deepseek": rig.cheap, "jev": rig.judge})
    args = {"role": Role.WRITER, "prompt_key": "test.post", "variables": VARS, "schema": Post,
            "account_id": rig.account_id, "purpose": "draft.generate"}  # fmt: skip
    with pytest.raises(PermanentProviderError) as e401:
        await gw.generate(**args)  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailable) as e500:
        await gw.generate(**args)  # type: ignore[arg-type]
    for exc in (e401.value, e500.value):
        chain: BaseException | None = exc
        while chain is not None:
            assert secret not in str(chain) and secret not in repr(chain)
            chain = chain.__cause__ or chain.__context__
    rows = await rig.rows("SELECT * FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    events = await rig.rows("SELECT * FROM events WHERE account_id = :a", a=rig.account_id)
    assert rows and events
    dump = repr([tuple(r) for r in rows]) + repr([tuple(e) for e in events])
    assert secret not in dump
    assert "sk-ant" not in dump


async def test_model_change_is_one_line_in_models_toml(rig: Rig, tmp_path: Path) -> None:
    original = DEFAULT_MODELS_PATH.read_text().splitlines()
    changed = [line.replace('"claude-opus-5-5"', '"claude-sonnet-5-5"') for line in original]
    assert sum(a != b for a, b in zip(original, changed, strict=True)) == 1
    path = tmp_path / "models.toml"
    path.write_text("\n".join(changed) + "\n")

    rig.writer.push(GOOD)
    gw = rig.gateway(models=ModelsConfig.load(path))
    await gw.generate(
        role=Role.WRITER, prompt_key="test.post", variables=VARS, schema=Post,
        account_id=rig.account_id, purpose="draft.generate",
    )  # fmt: skip
    assert rig.writer.calls[0].model == "claude-sonnet-5-5"
    [row] = await rig.rows("SELECT model FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    assert row.model == "claude-sonnet-5-5"


async def test_decide_uses_yes_answers_helper(rig: Rig) -> None:
    rig.judge.push(yes_answers(["on_topic"]))
    verdict = await rig.gateway().decide(
        state="x", questions={"on_topic": QUESTIONS["on_topic"]}, account_id=rig.account_id,
        purpose="draft.judge",
    )  # fmt: skip
    assert verdict.answers["on_topic"] == NoulA(p=0.9)
