"""Писатель через Claude Code CLI (`claude -p`) на подписке владельца.

Вызов неинтерактивный: промпт — через stdin, инструменты отключены, один ход, сессия не
сохраняется, настройки и CLAUDE.md пользователя не подмешиваются. Из окружения процесса удалены
`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`: иначе CLI пошёл бы в платный API. Стоимость вызова —
0 (подписка), в `llm_calls` пишутся токены и задержка. Одновременные вызовы ограничены семафором.

Ошибки: лимит подписки, ненулевой код выхода → `SubscriptionLimit` (временная и «недоступен»:
шлюз повторит и разомкнёт предохранитель); таймаут → временная; нет бинаря, не залогинен,
прочие ошибки CLI и мусор в ответе → постоянная. Тексты ошибок — фиксированные метки, без
вывода CLI и окружения.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from xcopilot.llm.errors import (
    PermanentProviderError,
    ProviderError,
    SubscriptionLimit,
    TransientProviderError,
)
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.base import ProviderResult, TextRequest

DROPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
LIMIT_MARKERS = ("hit your limit", "usage limit", "rate limit", "limit reached", "overloaded")
LOGIN_MARKERS = ("not logged in", "/login", "invalid api key", "authentication")


@dataclass(frozen=True)
class CliOutput:
    returncode: int
    stdout: str
    stderr: str = ""


class CliRunner(Protocol):
    async def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: str,
        env: Mapping[str, str],
        timeout: float,  # noqa: ASYNC109
    ) -> CliOutput: ...


class SubprocessRunner:
    """Настоящий запуск процесса; по таймауту процесс убивается и бросается TimeoutError."""

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: str,
        env: Mapping[str, str],
        timeout: float,  # noqa: ASYNC109
    ) -> CliOutput:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return CliOutput(
            proc.returncode if proc.returncode is not None else -1,
            out.decode(errors="replace"),
            err.decode(errors="replace"),
        )


def build_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Копия окружения без ключей платного API."""
    return {k: v for k, v in environ.items() if k not in DROPPED_ENV}


def build_argv(binary: str, model: str, system: str) -> list[str]:
    return [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--system-prompt",
        system,
        "--max-turns",
        "1",
        "--tools",
        "",
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources",
        "",
    ]


def _stdin(request: TextRequest) -> str:
    if len(request.messages) == 1:
        return request.messages[0].content
    labels = {"user": "Пользователь", "assistant": "Ассистент"}
    return "\n\n".join(f"{labels[m.role]}:\n{m.content}" for m in request.messages)


def _is_limit(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in LIMIT_MARKERS)


class ClaudeCliProvider:
    name = "claude_cli"

    def __init__(
        self,
        runner: CliRunner | None = None,
        *,
        binary: str = "claude",
        timeout: float = 180.0,
        concurrency: int = 1,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._runner: CliRunner = runner or SubprocessRunner()
        self._binary = binary
        self._timeout = timeout
        self._environ = environ
        self._semaphore = asyncio.Semaphore(concurrency)

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        if any(m.images for m in request.messages):
            raise PermanentProviderError(self.name, kind="images_unsupported")
        system = "\n\n".join(block.text for block in request.system)
        argv = build_argv(self._binary, model, system)
        env = build_env(os.environ if self._environ is None else self._environ)
        error: ProviderError | None = None
        output: CliOutput | None = None
        async with self._semaphore:
            try:
                output = await self._runner(
                    argv, stdin=_stdin(request), env=env, timeout=self._timeout
                )
            except TimeoutError:
                error = TransientProviderError(self.name, kind="timeout")
            except FileNotFoundError:
                error = PermanentProviderError(self.name, kind="cli_not_found")
        if error is not None:
            raise error  # вне except: без __context__
        assert output is not None
        return self._parse(output, model)

    def _parse(self, output: CliOutput, model: str) -> ProviderResult:
        data: Any = None
        with contextlib.suppress(ValueError):
            data = json.loads(output.stdout)
        body = data if isinstance(data, dict) else {}
        text = str(body.get("result", "")) if isinstance(body.get("result"), str) else ""
        if output.returncode != 0:
            kind = "limit" if _is_limit(text) or _is_limit(output.stderr) else "exit_code"
            raise SubscriptionLimit(self.name, kind=kind)
        if not body:
            raise PermanentProviderError(self.name, kind="bad_output")
        if body.get("is_error"):
            if _is_limit(text) or body.get("api_error_status") == 429:
                raise SubscriptionLimit(self.name, kind="limit")
            lowered = text.lower()
            if any(marker in lowered for marker in LOGIN_MARKERS):
                raise PermanentProviderError(self.name, kind="not_logged_in")
            raise PermanentProviderError(self.name, kind="cli_error")
        if "result" not in body:
            raise PermanentProviderError(self.name, kind="bad_output")
        raw = body.get("usage")
        u: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
        usage = Usage(
            input_tokens=int(u.get("input_tokens", 0)),
            output_tokens=int(u.get("output_tokens", 0)),
            cache_read_tokens=int(u.get("cache_read_input_tokens", 0)),
            cache_write_tokens=int(u.get("cache_creation_input_tokens", 0)),
        )
        used = body.get("modelUsage")
        name = next(iter(used), model) if isinstance(used, dict) and used else model
        return ProviderResult(text=text, usage=usage, model=name)
