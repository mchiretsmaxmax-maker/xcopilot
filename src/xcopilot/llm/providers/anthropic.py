"""Адаптер Anthropic (writer): Messages API, кэшируемые системные блоки.

SDK сам не повторяет (max_retries=0): повторы и предохранитель — в шлюзе. Ошибки SDK
превращаются в свои без цепочки исключений: тело ответа может содержать ключ.
"""

from __future__ import annotations

import anthropic
import httpx2
from anthropic.types import (
    AssistantMessageParam,
    MessageParam,
    TextBlockParam,
    UserMessageParam,
)

from xcopilot.llm.errors import ProviderError, TransientProviderError, status_error
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.base import ProviderResult, TextRequest


def system_blocks(request: TextRequest) -> list[TextBlockParam]:
    blocks: list[TextBlockParam] = []
    for block in request.system:
        item: TextBlockParam = {"type": "text", "text": block.text}
        if block.cacheable:
            item["cache_control"] = {"type": "ephemeral"}
        blocks.append(item)
    return blocks


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx2.AsyncClient | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=timeout,
            http_client=http_client,
        )

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        messages: list[MessageParam] = []
        for m in request.messages:
            if m.role == "user":
                messages.append(UserMessageParam(role="user", content=m.content))
            else:
                messages.append(AssistantMessageParam(role="assistant", content=m.content))
        error: ProviderError | None = None
        try:
            msg = await self._client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                system=system_blocks(request),
                messages=messages,
            )
        except anthropic.APIStatusError as exc:
            error = status_error(self.name, exc.status_code)
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            error = TransientProviderError(self.name, kind=type(exc).__name__)
        if error is not None:
            raise error  # вне except: без __context__ с телом ответа
        text = "".join(block.text for block in msg.content if block.type == "text")
        u = msg.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        return ProviderResult(text=text, usage=usage, model=msg.model)
