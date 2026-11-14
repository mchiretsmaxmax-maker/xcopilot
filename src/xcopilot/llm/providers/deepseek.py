"""Адаптер DeepSeek (cheap, резервный судья) через OpenAI-совместимый API."""

from __future__ import annotations

import base64

import httpx2
import openai
from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam

from xcopilot.llm.errors import ProviderError, TransientProviderError, status_error
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.base import Image, ProviderResult, TextRequest

BASE_URL = "https://api.deepseek.com"


def data_url(image: Image) -> str:
    """Картинка как data URL (vision в Chat Completions)."""
    return f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        *,
        http_client: httpx2.AsyncClient | None = None,
        base_url: str = BASE_URL,
        timeout: float = 120.0,
    ) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=timeout,
            http_client=http_client,
        )

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        # у DeepSeek кэш префикса автоматический: блоки склеиваются в одно системное сообщение
        messages: list[ChatCompletionMessageParam] = []
        if system := "\n\n".join(block.text for block in request.system):
            messages.append({"role": "system", "content": system})
        for m in request.messages:
            if m.role == "user" and m.images:
                parts: list[ChatCompletionContentPartParam] = [{"type": "text", "text": m.content}]
                parts += [
                    {"type": "image_url", "image_url": {"url": data_url(img)}} for img in m.images
                ]
                messages.append({"role": "user", "content": parts})
            elif m.role == "user":
                messages.append({"role": "user", "content": m.content})
            else:
                messages.append({"role": "assistant", "content": m.content})
        error: ProviderError | None = None
        try:
            if request.json_mode:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=request.max_tokens,
                    response_format={"type": "json_object"},
                )
            else:
                resp = await self._client.chat.completions.create(
                    model=model, messages=messages, max_tokens=request.max_tokens
                )
        except openai.APIStatusError as exc:
            error = status_error(self.name, exc.status_code)
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            error = TransientProviderError(self.name, kind=type(exc).__name__)
        if error is not None:
            raise error  # вне except: без __context__ с телом ответа
        text = resp.choices[0].message.content or "" if resp.choices else ""
        usage = (
            Usage(input_tokens=resp.usage.prompt_tokens, output_tokens=resp.usage.completion_tokens)
            if resp.usage is not None
            else Usage()
        )
        return ProviderResult(text=text, usage=usage, model=resp.model)
