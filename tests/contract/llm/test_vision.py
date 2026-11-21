"""ТЗ v3.1: картинки в сообщении — DeepSeek отправляет их блоками."""

from __future__ import annotations

import base64

import pytest

from tests.contract.llm.test_cassettes import KEY, Recorder
from xcopilot.llm.errors import PermanentProviderError
from xcopilot.llm.providers.base import Image, Message, PromptBlock, TextRequest
from xcopilot.llm.providers.codex_cli import CodexCliProvider
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


async def test_codex_writer_rejects_image_request() -> None:
    with pytest.raises(PermanentProviderError, match="images_unsupported"):
        await CodexCliProvider().complete(model="gpt-6.2-sol", request=REQUEST)
