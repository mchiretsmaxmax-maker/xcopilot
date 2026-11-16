"""Эталонный eval-набор (этап 15, задача 2; ADR 0028): схема baseline, загрузка эталона, прогон.

Каркас готов в 15a; сам замер (`--record`) делает этап 15b на настоящих постах владельца
и с ключами моделей. До этого `baseline.json` лежит со `status: "pending"` и пустыми числами —
схема это допускает, а тест `test_eval_baseline_file_exists_and_valid` проверяет строго.

Запуск (15b, на сервере или локально с .env):
    uv run python -m tests.evals.run_evals --account <handle> --check     # сравнить с baseline
    uv run python -m tests.evals.run_evals --account <handle> --record    # записать baseline

Метрики (все «больше — лучше»):
- `gate_pass_rate` — доля вариантов, прошедших ворота без владельца;
- `judge_voice_mean`, `judge_hook_mean` — средние оценки судьи 0..3;
- `judge_ai_smell_mean` — средняя оценка «не пахнет ИИ» 0..3 (3 — не пахнет);
- `zero_edit_rate` — доля карточек, принятых без правки, за 7 дней живой работы (15b, из
  метрики `zero_edit_rate` в metric_values; офлайн не меряется).
Регрессия — любая метрика хуже baseline больше чем на `regression_tolerance` (10%).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

HERE = Path(__file__).resolve().parent
BASELINE_PATH = HERE / "baseline.json"
TOPICS_PATH = HERE / "golden_topics.yaml"
POSTS_PATH = HERE / "golden_posts.yaml"

SCHEMA_VERSION = 1
STATUSES = ("pending", "recorded")
METRIC_KEYS = (
    "zero_edit_rate",
    "gate_pass_rate",
    "judge_voice_mean",
    "judge_hook_mean",
    "judge_ai_smell_mean",
)
OFFLINE_KEYS = METRIC_KEYS[1:]
ABLATION_ARMS = ("with_knowledge", "without_knowledge")
ABLATION_KEYS = ("judge_voice_mean", "judge_hook_mean")
TOP_KEYS = ("schema_version", "status", "recorded_at", "dataset", "models", "metrics", "ablation",
            "regression_tolerance")  # fmt: skip
GOLDEN_SIZE = 30


# ── схема baseline ──────────────────────────────────────────────────────────────────────


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _ablation_problems(ablation: object) -> list[str]:
    if not isinstance(ablation, dict):
        return ["ablation: ожидается объект с with_knowledge / without_knowledge / n_topics"]
    problems: list[str] = []
    for arm in ABLATION_ARMS:
        values = ablation.get(arm)
        if not isinstance(values, dict):
            problems.append(f"ablation.{arm}: нет")
            continue
        problems += [f"ablation.{arm}.{k}: не число" for k in ABLATION_KEYS
                     if not _is_number(values.get(k))]  # fmt: skip
    if not isinstance(ablation.get("n_topics"), int) or ablation["n_topics"] < 1:
        problems.append("ablation.n_topics: ожидается целое ≥ 1")
    return problems


def validate_baseline(data: dict[str, Any]) -> list[str]:
    """Список нарушений схемы baseline.json; пустой — файл годен."""
    problems = [f"нет ключа {k}" for k in TOP_KEYS if k not in data]
    if problems:
        return problems
    if data["schema_version"] != SCHEMA_VERSION:
        problems.append(f"schema_version: ожидается {SCHEMA_VERSION}")
    status = data["status"]
    if status not in STATUSES:
        return [*problems, f"status: {status!r} не из {STATUSES}"]
    metrics = data["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != set(METRIC_KEYS):
        return [*problems, f"metrics: нужны ровно ключи {', '.join(METRIC_KEYS)}"]
    dataset = data["dataset"]
    if not isinstance(dataset, dict) or not {"topics", "posts", "provisional"} <= set(dataset):
        problems.append("dataset: нужны topics, posts, provisional")
        dataset = {}
    tolerance = data["regression_tolerance"]
    if not _is_number(tolerance) or not 0 < tolerance < 1:
        problems.append("regression_tolerance: число в (0, 1)")
    if status == "pending":
        problems += [f"metrics.{k}: до замера должно быть null" for k, v in metrics.items()
                     if v is not None]  # fmt: skip
        if data["ablation"] is not None:
            problems.append("ablation: до замера должно быть null")
        if data["recorded_at"] is not None:
            problems.append("recorded_at: до замера должно быть null")
        return problems
    problems += [f"metrics.{k}: не число" for k, v in metrics.items() if not _is_number(v)]
    if not isinstance(data["recorded_at"], str):
        problems.append("recorded_at: дата замера ISO 8601")
    if dataset.get("provisional") is not False:
        problems.append("dataset.provisional: замер только на настоящих постах владельца")
    problems += _ablation_problems(data["ablation"])
    return problems


def pending_baseline() -> dict[str, Any]:
    """Пустой baseline до замера (так он лежит в репозитории до 15b)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pending",
        "recorded_at": None,
        "dataset": {"topics": TOPICS_PATH.name, "posts": POSTS_PATH.name, "provisional": True},
        "models": {"writer": None, "judge": None},
        "metrics": dict.fromkeys(METRIC_KEYS),
        "ablation": None,
        "regression_tolerance": 0.10,
    }


# ── эталон ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GoldenTopic:
    id: str
    topic: str
    format: str = "post"
    lang: str = "ru"


@dataclass(frozen=True, slots=True)
class GoldenPost:
    id: str
    text: str
    url: str | None = None


def _load(path: Path, key: str) -> tuple[bool, list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text()) or {}
    items = data.get(key) or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError(f"{path.name}: {key} — список объектов")
    return bool(data.get("provisional", True)), items


def load_topics(path: Path = TOPICS_PATH) -> tuple[bool, list[GoldenTopic]]:
    provisional, items = _load(path, "topics")
    return provisional, [GoldenTopic(**i) for i in items]


def load_posts(path: Path = POSTS_PATH) -> tuple[bool, list[GoldenPost]]:
    provisional, items = _load(path, "posts")
    return provisional, [GoldenPost(**i) for i in items]


# ── прогон ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Scored:
    """Один вариант после ворот: прошёл ли без владельца и оценки судьи (0..3 или None)."""

    passed: bool
    voice: float | None
    hook: float | None
    ai_smell: float | None


Generate = Callable[[GoldenTopic], Awaitable[Sequence[Scored]]]


@dataclass
class EvalResult:
    metrics: dict[str, float | None] = field(default_factory=dict)
    n_topics: int = 0
    n_variants: int = 0


def _mean(values: Sequence[float | None]) -> float | None:
    real = [v for v in values if v is not None]
    return round(sum(real) / len(real), 4) if real else None


async def evaluate(topics: Sequence[GoldenTopic], generate: Generate) -> EvalResult:
    """Прогнать темы через писателя и ворота (generate) и посчитать офлайн-метрики."""
    scored: list[Scored] = []
    for topic in topics:
        scored += list(await generate(topic))
    n = len(scored)
    return EvalResult(
        metrics={
            "gate_pass_rate": round(sum(s.passed for s in scored) / n, 4) if n else None,
            "judge_voice_mean": _mean([s.voice for s in scored]),
            "judge_hook_mean": _mean([s.hook for s in scored]),
            "judge_ai_smell_mean": _mean([s.ai_smell for s in scored]),
        },
        n_topics=len(topics),
        n_variants=n,
    )


def regressions(
    current: dict[str, float | None], baseline: dict[str, Any], keys: Sequence[str] = OFFLINE_KEYS
) -> list[str]:
    """eval_regression_gate: метрики хуже baseline больше чем на regression_tolerance."""
    if baseline.get("status") != "recorded":
        return []  # не с чем сравнивать: baseline запишет 15b
    tolerance = float(baseline["regression_tolerance"])
    found: list[str] = []
    for key in keys:
        base, now = baseline["metrics"].get(key), current.get(key)
        if base is None or now is None:
            continue
        if now < base * (1 - tolerance):
            found.append(f"{key}: {now:.3f} < {base:.3f} − {tolerance:.0%}")
    return found


def recorded_baseline(
    result: EvalResult,
    *,
    zero_edit_rate: float,
    ablation: dict[str, Any],
    models: dict[str, str],
    provisional: bool,
) -> dict[str, Any]:
    data = pending_baseline()
    data.update(
        status="recorded",
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        models=models,
        ablation=ablation,
    )
    data["dataset"]["provisional"] = provisional
    data["metrics"] = {**result.metrics, "zero_edit_rate": zero_edit_rate}
    return data


# ── связка с приложением (15b) ──────────────────────────────────────────────────────────


def gate_generate(app: Any, account_id: UUID, *, writer: Any | None = None) -> Generate:
    """generate для собранного приложения: писатель (по умолчанию app.writer) → ворота."""
    from xcopilot.brain.schemas import TaskSpec

    async def run(topic: GoldenTopic) -> list[Scored]:
        task = TaskSpec(format=topic.format, lang=topic.lang, topic=topic.topic)
        gen = await (writer or app.writer).generate(account_id, task)
        out: list[Scored] = []
        for draft in gen.drafts:
            decision = await app.gate.run_gate(account_id, draft.id)
            v = decision.verdict
            out.append(
                Scored(
                    passed=decision.outcome == "pass",
                    voice=v.voice_match.score if v else None,
                    hook=v.hook.score if v else None,
                    ai_smell=v.ai_smell.score if v else None,
                )
            )
        return out

    return run


async def _main(args: argparse.Namespace) -> int:  # pragma: no cover - живой прогон (15b)
    from tests.evals.ablation import run_ablation
    from xcopilot.app import build_app
    from xcopilot.config import load_settings
    from xcopilot.db.repos import AccountsRepo, MetricValuesRepo

    provisional, topics = load_topics()
    posts_provisional, _ = load_posts()
    baseline = json.loads(BASELINE_PATH.read_text())
    app = build_app(load_settings())
    try:
        await app.startup()
        async with app.uow_factory() as uow:
            acc = await AccountsRepo(uow.session).get_by_handle(args.account)
            if acc is None:
                print(f"нет аккаунта {args.account}")
                return 2
            zero_edit = await MetricValuesRepo(uow.session, acc.id).latest("zero_edit_rate")
        result = await evaluate(topics, gate_generate(app, acc.id))
        print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
        if args.record:
            if zero_edit is None:
                print("zero_edit_rate ещё не посчитан: нужна неделя живой работы")
                return 2
            ablation = await run_ablation(app, acc.id, topics)
            data = recorded_baseline(
                result,
                zero_edit_rate=zero_edit,
                ablation=ablation,
                models=app.model_names(),
                provisional=provisional or posts_provisional,
            )
            problems = validate_baseline(data)
            if problems:
                print("\n".join(problems))
                return 1
            BASELINE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            print(f"baseline записан: {BASELINE_PATH}")
            return 0
        found = regressions(result.metrics, baseline)
        print("\n".join(found) or "регрессий нет")
        return 1 if found else 0
    finally:
        await app.aclose()


def main() -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="Эталонный eval xcopilot (этап 15b)")
    parser.add_argument("--account", required=True, help="handle аккаунта в X")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="сравнить с baseline")
    mode.add_argument("--record", action="store_true", help="записать baseline")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
