"""Писатель через Claude Code CLI на подписке (заглушка: реализация в следующем коммите)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from xcopilot.llm.providers.base import ProviderResult, TextRequest


@dataclass(frozen=True)
class CliOutput:
    returncode: int
    stdout: str
    stderr: str = ""


class CliRunner(Protocol):
    async def __call__(
        self, argv: Sequence[str], *, stdin: str, env: Mapping[str, str], timeout: float
    ) -> CliOutput: ...


class SubprocessRunner:
    async def __call__(
        self, argv: Sequence[str], *, stdin: str, env: Mapping[str, str], timeout: float
    ) -> CliOutput:
        raise NotImplementedError


def build_env(environ: Mapping[str, str]) -> dict[str, str]:
    raise NotImplementedError


def build_argv(binary: str, model: str, system: str) -> list[str]:
    raise NotImplementedError


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
        raise NotImplementedError

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult:
        raise NotImplementedError
