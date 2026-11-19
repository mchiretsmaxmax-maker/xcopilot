"""DeepSeek v4-pro по умолчанию «думает» и тратит весь max_tokens на рассуждение — ответ пустой
(проверено eval 19.11.2026, ветка feat/reply-gate-strict). Режим задаёт конфиг роли."""

from __future__ import annotations

from dataclasses import replace

from tests.contract.llm.test_cassettes import KEY, Recorder
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.providers.base import Message, PromptBlock, TextRequest
from xcopilot.llm.providers.deepseek import DeepSeekProvider
from xcopilot.llm.roles import Role
from xcopilot.quality.judge import PURPOSE, REPLY_PURPOSE

REQUEST = TextRequest(
    system=(PromptBlock("Судья."),), messages=(Message("user", "Текст"),), json_mode=True
)


async def test_thinking_disabled_is_sent() -> None:
    rec = Recorder("deepseek_chat")
    await DeepSeekProvider(KEY, http_client=rec.client()).complete(
        model="deepseek-v4-pro", request=replace(REQUEST, thinking=False)
    )
    assert rec.body()["thinking"] == {"type": "disabled"}


async def test_thinking_not_sent_by_default() -> None:
    rec = Recorder("deepseek_chat")
    await DeepSeekProvider(KEY, http_client=rec.client()).complete(
        model="deepseek-flash", request=REQUEST
    )
    assert "thinking" not in rec.body()


def test_reply_judge_runs_without_thinking() -> None:
    judge = ModelsConfig.load().roles[Role.JUDGE]
    assert judge.thinking is None and judge.for_purpose(PURPOSE).thinking is None
    assert judge.for_purpose(REPLY_PURPOSE).thinking is False
