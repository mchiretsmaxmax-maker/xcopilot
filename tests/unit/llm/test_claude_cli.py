"""Провайдер Claude Code CLI (подписка): разбор JSON, ошибки, окружение, стоимость, конфиг."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xcopilot.llm.errors import (
    PermanentProviderError,
    ProviderUnavailable,
    TransientProviderError,
)
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable, Usage, price
from xcopilot.llm.providers.base import Message, PromptBlock, ProviderResult, TextRequest
from xcopilot.llm.providers.claude_cli import (
    ClaudeCliProvider,
    CliOutput,
    SubprocessRunner,
    build_argv,
    build_env,
)
from xcopilot.llm.roles import Role

LEGACY = Path(__file__).parents[2] / "fixtures" / "models_anthropic_jev.toml"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

OK_JSON = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "pong",
        "usage": {
            "input_tokens": 2,
            "output_tokens": 4,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 900,
        },
        "modelUsage": {"claude-opus-5-5": {}},
    }
)


class FakeRunner:
    def __init__(self, *outputs: CliOutput | Exception) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[list[str], str, dict[str, str], float]] = []

    async def __call__(
        self, argv: Sequence[str], *, stdin: str, env: Mapping[str, str], timeout: float
    ) -> CliOutput:
        self.calls.append((list(argv), stdin, dict(env), timeout))
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def request(text: str = "Скажи pong") -> TextRequest:
    return TextRequest(
        system=(PromptBlock("Ты автор."), PromptBlock("Пиши коротко.")),
        messages=(Message("user", text),),
    )


async def run(runner: FakeRunner, **kw: object) -> ProviderResult:
    provider = ClaudeCliProvider(runner, **kw)  # type: ignore[arg-type]
    return await provider.complete(model="claude-opus-5-5", request=request())


async def test_success_parses_text_usage_model() -> None:
    result = await run(FakeRunner(CliOutput(0, OK_JSON)))
    assert result.text == "pong"
    assert result.usage == Usage(
        input_tokens=2, output_tokens=4, cache_read_tokens=10, cache_write_tokens=900
    )
    assert result.model == "claude-opus-5-5"


async def test_command_line_and_stdin() -> None:
    runner = FakeRunner(CliOutput(0, OK_JSON))
    await run(runner, binary="/opt/claude", timeout=77.0)
    argv, stdin, _, timeout = runner.calls[0]
    assert argv[0] == "/opt/claude"
    for flag in ("-p", "--output-format", "--system-prompt", "--max-turns", "--tools"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--system-prompt") + 1] == "Ты автор.\n\nПиши коротко."
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert argv[argv.index("--tools") + 1] == ""
    assert "Скажи pong" not in " ".join(argv)  # промпт идёт через stdin
    assert stdin == "Скажи pong"
    assert timeout == 77.0


async def test_repair_dialogue_is_flattened_into_stdin() -> None:
    runner = FakeRunner(CliOutput(0, OK_JSON))
    req = TextRequest(
        system=(),
        messages=(Message("user", "AAA"), Message("assistant", "BBB"), Message("user", "CCC")),
    )
    await ClaudeCliProvider(runner).complete(model="m", request=req)
    stdin = runner.calls[0][1]
    assert stdin.index("AAA") < stdin.index("BBB") < stdin.index("CCC")


def test_env_drops_api_credentials() -> None:
    env = build_env(
        {"ANTHROPIC_API_KEY": "sk-1", "ANTHROPIC_AUTH_TOKEN": "t", "PATH": "/bin", "HOME": "/h"}
    )
    assert env == {"PATH": "/bin", "HOME": "/h"}


async def test_provider_passes_clean_env() -> None:
    runner = FakeRunner(CliOutput(0, OK_JSON))
    await run(runner, environ={"ANTHROPIC_API_KEY": "sk-secret", "PATH": "/bin"})
    assert "ANTHROPIC_API_KEY" not in runner.calls[0][2]
    assert runner.calls[0][2]["PATH"] == "/bin"


def test_build_argv_is_isolated() -> None:
    argv = build_argv("claude", "m", "sys")
    assert "--no-session-persistence" in argv and "--disable-slash-commands" in argv


@pytest.mark.parametrize(
    "message",
    ["You've hit your limit · resets 5pm", "Claude usage limit reached", "rate limit exceeded"],
)
async def test_subscription_limit_is_unavailable(message: str) -> None:
    body = json.dumps({"is_error": True, "result": message, "api_error_status": 429})
    with pytest.raises(ProviderUnavailable):
        await run(FakeRunner(CliOutput(1, body)))
    with pytest.raises(TransientProviderError):  # шлюз повторит и разомкнёт предохранитель
        await run(FakeRunner(CliOutput(0, body)))


async def test_nonzero_exit_is_unavailable_without_leaks() -> None:
    secret = "sk-ant-LEAK-123"
    with pytest.raises(ProviderUnavailable) as exc:
        await run(FakeRunner(CliOutput(2, "", f"boom {secret}")))
    assert secret not in str(exc.value) and secret not in repr(exc.value)


async def test_other_cli_error_is_permanent_and_clean() -> None:
    body = json.dumps({"is_error": True, "result": "Invalid API key sk-ant-LEAK-123"})
    with pytest.raises(PermanentProviderError) as exc:
        await run(FakeRunner(CliOutput(0, body)))
    assert "sk-ant" not in str(exc.value)


async def test_garbage_output_is_permanent() -> None:
    with pytest.raises(PermanentProviderError):
        await run(FakeRunner(CliOutput(0, "not json")))
    with pytest.raises(PermanentProviderError):
        await run(FakeRunner(CliOutput(0, json.dumps({"is_error": False}))))


async def test_timeout_is_transient() -> None:
    with pytest.raises(TransientProviderError) as exc:
        await run(FakeRunner(TimeoutError()))
    assert exc.value.kind == "timeout"


async def test_missing_binary_is_permanent() -> None:
    with pytest.raises(PermanentProviderError) as exc:
        await run(FakeRunner(FileNotFoundError("claude")))
    assert exc.value.kind == "cli_not_found"


async def test_concurrency_limited_by_semaphore() -> None:
    active = 0
    peak = 0

    async def runner(
        argv: Sequence[str], *, stdin: str, env: Mapping[str, str], timeout: float
    ) -> CliOutput:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return CliOutput(0, OK_JSON)

    provider = ClaudeCliProvider(runner)
    await asyncio.gather(*(provider.complete(model="m", request=request()) for _ in range(4)))
    assert peak == 1
    active = peak = 0
    provider = ClaudeCliProvider(runner, concurrency=2)
    await asyncio.gather(*(provider.complete(model="m", request=request()) for _ in range(4)))
    assert peak == 2


async def test_subprocess_runner_real_process() -> None:
    out = await SubprocessRunner()(["/bin/cat"], stdin="привет", env={}, timeout=5)
    assert (out.returncode, out.stdout) == (0, "привет")
    with pytest.raises(TimeoutError):
        await SubprocessRunner()(
            [sys.executable, "-c", "import time; time.sleep(5)"], stdin="", env={}, timeout=0.3
        )


def test_cost_is_zero_for_subscription() -> None:
    usage = Usage(input_tokens=10**6, output_tokens=10**6, cache_read_tokens=10**6)
    assert price("claude_cli", "claude-opus-5-5", usage, NOW) == 0
    assert price("claude_cli", "any-new-model", usage, NOW) == 0


def test_shipped_config_uses_subscription_writer_and_deepseek_judge() -> None:
    models = ModelsConfig.load()
    writer, judge = models.roles[Role.WRITER], models.roles[Role.JUDGE]
    assert (writer.provider, writer.model) == ("claude_cli", "claude-opus-5-5")
    assert (judge.provider, judge.model) == ("deepseek", "deepseek-flash")
    assert PricingTable.load().lookup("claude_cli", "claude-opus-5-5").input == 0


def test_writer_switch_is_one_line(tmp_path: Path) -> None:
    original = LEGACY.read_text().splitlines()
    changed = [
        line.replace('provider = "anthropic"', 'provider = "claude_cli"') for line in original
    ]
    assert sum(a != b for a, b in zip(original, changed, strict=True)) == 1
    path = tmp_path / "models.toml"
    path.write_text("\n".join(changed) + "\n")
    assert ModelsConfig.load(path).roles[Role.WRITER].provider == "claude_cli"
