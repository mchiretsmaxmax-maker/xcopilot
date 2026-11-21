"""Eval строгих ворот для ответов (ветка feat/reply-gate-strict). Живую БД только читает.

Что считает:
1. Набор `tests/fixtures/quality/ai_slop_replies.yaml`: детектор без формата и с format reply
   (шаблон advice работает только для reply/quote).
2. Последние N ответов из БД (drafts с вердиктом судьи): сохранённый вердикт через старую
   политику и через новую (строгий путь + детектор advice) — сколько ушло бы на переписывание.
3. --judge K: K черновиков заново судит deepseek-v4-pro (тот же промпт и вопросы, что в боте,
   пакет контекста из БД) — уверенность против flash. Вызовы идут мимо шлюза: в БД не пишем.
4. --regen K: K примеров «было → стало»: Codex CLI с промптом rewrite и замечаниями новых ворот.

Запуск:  uv run python scripts/eval_reply_gate.py [--limit 30] [--judge 10] [--regen 5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from xcopilot.llm.fallback_judge import PROMPT_KEY, parse_answers, prompt_variables
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.providers.base import Message, TextRequest
from xcopilot.llm.providers.codex_cli import CodexCliProvider
from xcopilot.llm.providers.deepseek import DeepSeekProvider
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import Verdict
from xcopilot.quality.instructions import to_rewrite_instructions
from xcopilot.quality.judge import (
    QUESTIONS_GATE2,
    REPLY_PURPOSE,
    JudgeVerdict,
    build_state,
    render_state,
)
from xcopilot.quality.lint.engine import Finding, LintDraft, LintReport
from xcopilot.quality.lint.slop import detect_slop
from xcopilot.quality.policy import Decision, Thresholds, decide
from xcopilot.retrieval.context_pack import PackIds, rehydrate
from xcopilot.writer import render

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "quality" / "ai_slop_replies.yaml"
LOCAL_DB = "@127.0.0.1:5433/"


def env() -> dict[str, str]:
    for path in (ROOT / ".env", ROOT.parents[1] / ".env"):
        if path.exists():
            return {k: v for k, v in dotenv_values(path).items() if v is not None}
    raise SystemExit("нет .env")


def fixture_report() -> list[str]:
    items = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["replies"]
    slop = [r["text"] for r in items if r["slop"]]
    clean = [r["text"] for r in items if not r["slop"]]
    out = []
    for fmt in (None, "reply"):
        caught = sum(1 for t in slop if detect_slop(t, "en", fmt=fmt))
        false = sum(1 for t in clean if detect_slop(t, "en", fmt=fmt))
        advice = sum(
            1
            for t in items
            if any(h.code == "advice" for h in detect_slop(t["text"], "en", fmt=fmt))
        )
        out.append(
            f"fixture fmt={fmt}: slop {caught}/{len(slop)}, ложных {false}/{len(clean)},"
            f" advice {advice}"
        )
    return out


def with_new_detector(row: Any) -> LintReport:
    stored = (row.lint_report or {}).get("findings", [])
    findings = [Finding.model_validate(f) for f in stored]
    have = {(f.rule, f.fragment) for f in findings}
    for h in detect_slop(row.text, "en", fmt=row.format):
        if ("ai_slop", h.fragment) not in have:
            findings.append(
                Finding(rule="ai_slop", level="error", message=h.message, fragment=h.fragment)
            )
    return LintReport(text=row.text, parts=list(row.parts or []), findings=findings)


def old_report(row: Any) -> LintReport:
    stored = (row.lint_report or {}).get("findings", [])
    return LintReport(
        text=row.text,
        parts=list(row.parts or []),
        findings=[Finding.model_validate(f) for f in stored],
    )


def why(d: Decision) -> str:
    return ", ".join(f"{r.code}:{r.fragment[:40]}" if r.fragment else r.code for r in d.reasons)


async def judge_v4(sf: Any, row: Any, key: str) -> JudgeVerdict:
    async with sf() as session:
        run = (
            await session.execute(
                text("SELECT context_pack FROM generation_runs WHERE id=:i"), {"i": row.run_id}
            )
        ).one()
        pack = await rehydrate(session, row.account_id, PackIds.model_validate(run.context_pack))
        await session.rollback()
    draft = LintDraft(text=row.text, format=row.format, parts=tuple(row.parts or []))
    state = render_state(build_state(draft, pack))
    rendered = PromptRegistry().render(PROMPT_KEY, prompt_variables(state, QUESTIONS_GATE2))
    rc = ModelsConfig.load().roles[Role.JUDGE].for_purpose(REPLY_PURPOSE)  # как в боте
    request = TextRequest(
        system=rendered.system,
        messages=(Message("user", rendered.user),),
        max_tokens=rc.max_tokens,
        json_mode=True,
        thinking=rc.thinking,
    )
    result = await DeepSeekProvider(key).complete(model=rc.model, request=request)
    answers = parse_answers(result.text, QUESTIONS_GATE2)
    return JudgeVerdict.from_verdict(Verdict(answers=answers, judge="deepseek-v4-pro"))


async def regen(sf: Any, row: Any, decision: Decision, cfg: dict[str, str]) -> str:
    async with sf() as session:
        run = (
            await session.execute(
                text("SELECT context_pack FROM generation_runs WHERE id=:i"), {"i": row.run_id}
            )
        ).one()
        pack = await rehydrate(session, row.account_id, PackIds.model_validate(run.context_pack))
        await session.rollback()
    sections = render.pack_sections(pack)
    lines = to_rewrite_instructions(decision, topic=pack.task.topic)
    rendered = PromptRegistry().render(
        "writer.rewrite.v1",
        {
            "passport": sections["passport"],
            "task": render.render_task(pack.task),
            "examples": sections.get("examples", ""),
            "facts": sections["facts"],
            "format": row.format,
            "draft": row.text,
            "instructions": "\n".join(f"- {ln}" for ln in lines),
        },
    )
    rc = ModelsConfig.load().roles[Role.WRITER]
    result = await CodexCliProvider(
        binary=cfg.get("CODEX_CLI_PATH") or "codex",
        effort=cfg.get("CODEX_CLI_EFFORT") or "medium",
        timeout=float(cfg.get("CODEX_CLI_TIMEOUT") or "180"),
    ).complete(
        model=rc.model,
        request=TextRequest(
            system=rendered.system,
            messages=(Message("user", rendered.user),),
            max_tokens=rc.max_tokens,
            json_mode=True,
        ),
    )
    return str(json.loads(result.text)["text"])


async def delay_report(sf: Any, before: float, after: float, pro_ms: float | None) -> None:
    """Задержка показа: средняя длительность вызовов из llm_calls (живые данные) и доля кругов.

    Варианты ответа проходят ворота по очереди (quality/jobs.py), карточка — после всех трёх.
    Модель: вариант = судья + доля_кругов × (переписывание + судья); один круг на переписанный.
    """
    async with sf() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT role, purpose, model, avg(latency_ms) AS ms, count(*) AS n"
                    " FROM llm_calls WHERE status = 'ok' AND latency_ms IS NOT NULL"
                    " AND purpose IN ('draft.judge', 'draft.rewrite')"
                    " GROUP BY role, purpose, model"
                )
            )
        ).all()
    ms = {(r.purpose, r.model): float(r.ms) for r in rows}
    for r in rows:
        print(f"  llm_calls {r.purpose} {r.model}: {float(r.ms) / 1000:.1f} с (n={r.n})")
    judge = ms.get(("draft.judge", "deepseek-flash"), 0.0)
    rewrite = next((v for (p, _), v in ms.items() if p == "draft.rewrite"), 0.0)
    pro = pro_ms if pro_ms is not None else judge
    print(f"  v4-pro (thinking off), замер eval: {pro / 1000:.1f} с")
    old = judge + before * (rewrite + judge)
    new = pro + after * (rewrite + pro)
    print(
        f"  задержка показа: вариант {old / 1000:.0f} с → {new / 1000:.0f} с;"
        f" карточка из 3 вариантов {3 * old / 1000:.0f} с → {3 * new / 1000:.0f} с"
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--judge", type=int, default=0)
    ap.add_argument("--regen", type=int, default=0)
    a = ap.parse_args()
    cfg = env()
    url = re.sub(r"@[^/]+/", LOCAL_DB, cfg["DATABASE_URL"])
    engine = create_async_engine(
        url, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
    )
    sf = async_sessionmaker(engine, expire_on_commit=False)
    for line in fixture_report():
        print(line)
    async with sf() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT d.id, d.account_id, d.run_id, d.format, d.text, d.parts, d.revision,"
                    " d.lint_report, d.judge_verdict FROM drafts d"
                    " WHERE d.format IN ('reply','quote') AND d.judge_verdict IS NOT NULL"
                    " ORDER BY d.created_at DESC LIMIT :n"
                ),
                {"n": a.limit},
            )
        ).all()
    t = Thresholds.load()
    before, after = Counter[str](), Counter[str]()
    reasons = Counter[str]()
    cases: list[tuple[Any, Decision]] = []
    for row in rows:
        v = JudgeVerdict.model_validate(row.judge_verdict)
        old = decide(old_report(row), v, t)
        new = decide(with_new_detector(row), v, t, format=row.format)
        before[old.outcome] += 1
        after[new.outcome] += 1
        reasons.update(r.code for r in new.reasons)
        if new.outcome == "rewrite":
            cases.append((row, new))
    print(f"\nБД: {len(rows)} ответов (сохранённые вердикты flash)")
    print(f"  до:    {dict(before)}")
    print(f"  после: {dict(after)}")
    print(f"  причины после: {dict(reasons.most_common())}")
    share_before = before["rewrite"] / max(1, len(rows))
    print(
        f"  доля на переписывание: до {share_before:.0%},"
        f" после {after['rewrite'] / max(1, len(rows)):.0%}"
    )
    for row, d in cases[:8]:
        print(f"  - [{row.id}] {row.text[:110]!r}\n      → {why(d)}")
    if a.judge:
        key = cfg["DEEPSEEK_API_KEY"]
        flash_unsure = pro_unsure = 0
        pro_ms: list[float] = []
        pro_after = Counter[str]()
        print(f"\nЖивой судья deepseek-v4-pro на {a.judge} ответах:")
        for row in rows[: a.judge]:
            old_v = JudgeVerdict.model_validate(row.judge_verdict)
            started = time.monotonic()
            try:
                pro = await judge_v4(sf, row, key)
                pro_ms.append((time.monotonic() - started) * 1000)
            except Exception as exc:  # eval: показать и идти дальше
                print(f"  - [{row.id}] ошибка {type(exc).__name__}: {exc}"[:200])
                continue
            d_old = decide(old_report(row), old_v, t)
            d_pro = decide(with_new_detector(row), pro, t, format=row.format)
            flash_unsure += "judge_unsure" in d_old.flags
            pro_unsure += "judge_unsure" in d_pro.flags
            pro_after[d_pro.outcome] += 1
            print(
                f"  - [{row.id}] flash vm {old_v.voice_match.score}/{old_v.voice_match.confidence}"
                f" hook {old_v.hook.score} slop {old_v.ai_slop}/{old_v.ai_slop_confidence} |"
                f" pro vm {pro.voice_match.score}/{pro.voice_match.confidence}"
                f" ais {pro.ai_smell.score}/{pro.ai_smell.confidence} hook {pro.hook.score}"
                f" slop {pro.ai_slop}/{pro.ai_slop_confidence} «{pro.ai_slop_quote[:40]}»"
                f" → {d_pro.outcome} ({why(d_pro)})"
            )
        print(
            f"  judge_unsure: flash {flash_unsure}, v4-pro {pro_unsure}; итог v4-pro:"
            f" {dict(pro_after)}"
        )
        share = pro_after["rewrite"] / max(1, sum(pro_after.values()))
        await delay_report(sf, share_before, share, statistics.mean(pro_ms) if pro_ms else None)
    if a.regen:
        print(f"\nБыло → стало (Codex CLI, промпт rewrite, {a.regen} примеров):")
        for row, d in cases[: a.regen]:
            new_text = await regen(sf, row, d, cfg)
            hits = [h.code for h in detect_slop(new_text, "en", fmt=row.format)]
            print(
                f"  было:  {row.text!r}\n  why:   {why(d)}\n  стало: {new_text!r}"
                f"\n  детектор после: {hits or 'чисто'}\n"
            )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
