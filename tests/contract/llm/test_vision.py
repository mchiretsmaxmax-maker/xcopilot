"""ТЗ v3.1: картинки в сообщении — DeepSeek и Anthropic отправляют их блоками, CLI отказывает."""

from __future__ import annotations

import base64

import pytest

from tests.contract.llm.test_cassettes import KEY, Recorder
from xcopilot.llm.errors import PermanentProviderError
from xcopilot.llm.providers.anthropic import AnthropicProvider
from xcopilot.llm.providers.base import Image, Message, PromptBlock, TextRequest
from xcopilot.llm.providers.claude_cli import ClaudeCliProvider, CliOutput
from xcopilot.llm.providers.deepseek import DeepSeekProvider, data_url

IMAGE = Image(data=b"\x89PNG-bytes", mime="image/png")
REQUEST = TextRequest(
    system=(PromptBlock("Прочитай скрин."),),
    messages=(Message("user", "Подпись: —", images=(IMAGE,)),),
    max_tokens=256,
    json_mode=True,
)
B64 = base64.b64encode(IMAGE.data).decode()


def test_data_url() -> None:
    assert data_url(IMAGE) == f"data:image/png;base64,{B64}"


async def test_deepseek_sends_image_parts() -> None:
    rec = Recorder("deepseek_chat")
    await DeepSeekProvider(KEY, http_client=rec.client()).complete(
        model="deepseek-flash", request=REQUEST
    )
    content = rec.body()["messages"][-1]["content"]
    assert content == [
        {"type": "text", "text": "Подпись: —"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{B64}"}},
    ]


async def test_anthropic_sends_image_blocks() -> None:
    rec = Recorder("anthropic_messages")
    await AnthropicProvider(KEY, http_client=rec.client()).complete(
        model="claude-opus-5-5", request=REQUEST
    )
    content = rec.body()["messages"][-1]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": B64},
    }
    assert content[1] == {"type": "text", "text": "Подпись: —"}


async def test_claude_cli_refuses_images() -> None:
    async def runner(*args: object, **kwargs: object) -> CliOutput:
        raise AssertionError("CLI не должен запускаться")

    with pytest.raises(PermanentProviderError) as info:
        await ClaudeCliProvider(runner).complete(model="claude-opus-5-5", request=REQUEST)
    assert info.value.kind == "images_unsupported"
