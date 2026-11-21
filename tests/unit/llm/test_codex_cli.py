"""Контракт с Codex CLI: изоляция, JSONL, токены и безопасные ошибки."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from xcopilot.llm.errors import PermanentProviderError, SubscriptionLimit, TransientProviderError
from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.base import Message, PromptBlock, TextRequest
from xcopilot.llm.providers.codex_cli import CliOutput, CodexCliProvider

MODEL = "gpt-6.2-sol"
REQUEST = TextRequest((PromptBlock("Пиши коротко."),), (Message("user", "Ответь pong"),))


def events(*rows: dict[str, object]) -> str:
    return "\n".join(json.dumps(row) for row in rows)


FINAL = {"type": "item.completed", "item": {"type": "agent_message", "text": "pong"}}
DONE = {
    "type": "turn.completed",
    "usage": {"input_tokens": 12, "cached_input_tokens": 10, "output_tokens": 4},
}


class Runner:
    def __init__(self, output: CliOutput | Exception) -> None:
        self.output = output
        self.argv: list[str] = []
        self.stdin = ""
        self.env: dict[str, str] = {}
        self.cwd: Path | None = None
        self.instructions = ""

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: str,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,  # noqa: ASYNC109
    ) -> CliOutput:
        self.argv, self.stdin, self.env, self.cwd = list(argv), stdin, dict(env), cwd
        self.instructions = (cwd / "instructions.md").read_text()
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


async def test_codex_reply_uses_ephemeral_restricted_turn_and_logs_token_usage() -> None:
    runner = Runner(CliOutput(0, events(FINAL, DONE)))
    provider = CodexCliProvider(
        runner,
        binary="/opt/codex",
        effort="medium",
        environ={
            "HOME": "/home/owner", "PATH": "/bin", "CODEX_HOME": "/opt/codex-home",
            "OPENAI_API_KEY": "secret", "BOT_TOKEN": "secret", "DATABASE_URL": "secret",
        },  # fmt: skip
    )
    result = await provider.complete(model=MODEL, request=REQUEST)
    assert result.text == "pong"
    assert result.model == MODEL
    assert result.usage == Usage(input_tokens=2, cache_read_tokens=10, output_tokens=4)
    assert runner.argv[:2] == ["/opt/codex", "exec"]
    assert runner.argv[-1] == "-"
    assert {"--ephemeral", "--ignore-user-config", "--json", "read-only", "never"} <= set(runner.argv)
    assert {'forced_login_method="chatgpt"', 'model_reasoning_effort="medium"'} <= set(runner.argv)
    assert {"shell_tool", "apps", "plugins", "hooks", "multi_agent"} <= set(runner.argv)
    assert 'web_search="disabled"' in runner.argv
    assert runner.stdin == "Ответь pong"
    assert "Пиши коротко." in runner.instructions and "Пиши коротко." not in " ".join(runner.argv)
    assert runner.cwd is not None and not runner.cwd.exists()
    assert runner.env == {"HOME": "/home/owner", "PATH": "/bin", "CODEX_HOME": "/opt/codex-home"}


async def test_invalid_result_only_passed_to_repair_in_stdin() -> None:
    runner = Runner(CliOutput(0, events(FINAL, DONE)))
    request = TextRequest(
        (),
        (Message("user", "Первый запрос"), Message("assistant", "Ошибка"),
         Message("user", "Повтори в JSON")),
        json_mode=True,
    )  # fmt: skip
    await CodexCliProvider(runner).complete(model=MODEL, request=request)
    assert runner.stdin.index("Первый запрос") < runner.stdin.index("Ошибка")
    assert runner.stdin.index("Ошибка") < runner.stdin.index("Повтори в JSON")
    assert "JSON" in runner.instructions


@pytest.mark.parametrize(
    ("output", "error", "kind"),
    [
        (FileNotFoundError(), PermanentProviderError, "cli_not_found"),
        (PermissionError(), PermanentProviderError, "cli_not_runnable"),
        (TimeoutError(), TransientProviderError, "timeout"),
        (CliOutput(0, events(FINAL)), PermanentProviderError, "bad_output"),
        (CliOutput(1, "", "unknown option --secret"), PermanentProviderError, "cli_error"),
        (CliOutput(1, "", "HTTP 401: secret"), PermanentProviderError, "not_logged_in"),
        (CliOutput(1, "", "HTTP 429: secret"), SubscriptionLimit, "limit"),
        (CliOutput(1, "", "boom secret"), TransientProviderError, "exit_code"),
        (
            CliOutput(0, events(FINAL, {"type": "turn.failed", "error": {"message": "secret"}})),
            TransientProviderError,
            "exit_code",
        ),
        (
            CliOutput(0, events({"type": "item.started", "item": {"type": "command_execution"}},
                                FINAL, DONE)),
            PermanentProviderError,
            "unexpected_tool",
        ),
    ],
)  # fmt: skip
async def test_cli_failures_are_sanitized(
    output: CliOutput | Exception, error: type[Exception], kind: str
) -> None:
    with pytest.raises(error, match=kind) as failure:
        await CodexCliProvider(Runner(output)).complete(model=MODEL, request=REQUEST)
    assert "secret" not in str(failure.value) and failure.value.__context__ is None
