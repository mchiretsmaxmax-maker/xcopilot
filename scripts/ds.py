#!/usr/bin/env python3
# ruff: noqa: E501

"""Исполнитель DeepSeek: одна чёткая задача -> правки только в разрешённых файлах -> проверки -> коммит.

Запуск:  python3 scripts/ds.py <файл-задания.md> [--dir <рабочая папка>] [--model deepseek-v4-pro|deepseek-flash] [--thinking] [--no-commit]

Файл задания (markdown), разделы:
  ## Файлы      — какие существующие файлы можно менять (по строке `- путь`); их содержимое уходит модели
  ## Новые      — какие новые файлы можно создать (`- путь`)
  ## Контекст   — файлы только для чтения (`- путь`), модель их не меняет
  ## Задача     — что сделать, точными шагами
  ## Коммит     — одна строка сообщения коммита

Модель не ходит по проекту и не запускает команд: получает ровно перечисленное и возвращает
полное новое содержимое файлов. Всё вне списка скрипт отбрасывает. Затем ruff, mypy, pytest;
при красных проверках — до 2 кругов починки (те же файлы). Результат: коротким итогом в stdout
и в <папка>/.ds/RESULT.md. Ключ — DEEPSEEK_API_KEY из .env проекта.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.deepseek.com/chat/completions"
SYSTEM = """Ты исполнитель кода. Тебе дано задание и содержимое файлов. Правила:
- Меняй ТОЛЬКО файлы из списка «Файлы» и создавай ТОЛЬКО файлы из списка «Новые». Остальное не трогай.
- Делай ровно то, что в «Задаче». Никакого рефакторинга, переименований, лишних функций и комментариев.
- Формат ответа — только блоки, без пояснений вне блоков:
<<<FILE путь>>>
полное новое содержимое файла
<<<END>>>
- Файл отдавай целиком, даже если правка в одной строке. Не выдумывай API: опирайся на показанный код.
- Если задание невыполнимо без файла вне списка — верни один блок <<<NOTE>>> причина <<<END>>> и больше ничего."""
CHECKS = [
    ("ruff", ["uv", "run", "ruff", "check", "."]),
    ("format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("mypy", ["uv", "run", "mypy", "src"]),
    ("pytest", ["uv", "run", "pytest", "-q", "-x"]),
]


def section(text: str, name: str) -> str:
    m = re.search(rf"^## {name}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def paths(block: str) -> list[str]:
    return [m.group(1).strip().strip("`") for m in re.finditer(r"^[-*]\s+(\S.*)$", block, re.M)]


def api_key(root: Path) -> str:
    for line in (root / ".env").read_text().splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].split("#")[0].strip().strip('"')
    sys.exit("DEEPSEEK_API_KEY не найден в .env")


def call(key: str, model: str, messages: list[dict[str, str]], thinking: bool) -> tuple[str, dict]:
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "max_tokens": 32000,
            "temperature": 0,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
    ).encode()
    req = urllib.request.Request(
        URL, body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"DeepSeek HTTP {e.code}: {e.read().decode()[:300]}")
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def blocks(answer: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r"<<<FILE\s+(\S+?)>>>\n(.*?)\n?<<<END>>>", answer, re.S):
        out[m.group(1).strip("`")] = m.group(2) + "\n"
    return out


def run_checks(cwd: Path) -> tuple[bool, str]:
    log = []
    for name, cmd in CHECKS:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if p.returncode != 0:
            tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-40:])
            log.append(f"[{name}] ОШИБКА\n{tail}")
            return False, "\n\n".join(log)
        log.append(f"[{name}] ok")
    return True, "\n".join(log)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--thinking", action="store_true", help="режим рассуждений: дольше и дороже")
    ap.add_argument("--no-commit", action="store_true")
    a = ap.parse_args()
    cwd = Path(a.dir).resolve()
    text = Path(a.task).read_text()
    edit, new, ctx = (paths(section(text, n)) for n in ("Файлы", "Новые", "Контекст"))
    task, commit = section(text, "Задача"), section(text, "Коммит").splitlines()[0:1]
    if not task or not (edit or new):
        sys.exit("В задании нет «Задача» или «Файлы/Новые»")
    key = api_key(Path(__file__).resolve().parent.parent)

    def show(label: str, files: list[str]) -> str:
        parts = []
        for f in files:
            p = cwd / f
            parts.append(f"=== {label}: {f} ===\n{p.read_text() if p.exists() else '(нет файла)'}")
        return "\n\n".join(parts)

    prompt = (
        f"## Можно менять\n{chr(10).join(edit) or '-'}\n\n## Можно создать\n{chr(10).join(new) or '-'}\n\n"
        f"## Задача\n{task}\n\n{show('менять', edit)}\n\n{show('только чтение', ctx)}"
    )
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    allowed = set(edit) | set(new)
    t0, tokens, ok, log, rounds, touched = time.time(), 0, False, "", 0, set()
    for rounds in range(1, 4):  # noqa: B007
        answer, usage = call(key, a.model, messages, a.thinking)
        tokens += usage.get("total_tokens", 0)
        got = blocks(answer)
        if not got:
            log = "Модель не вернула файлов: " + answer[:400]
            break
        dropped = [p for p in got if p not in allowed]
        for p, content in got.items():
            if p in allowed:
                (cwd / p).parent.mkdir(parents=True, exist_ok=True)
                (cwd / p).write_text(content)
                touched.add(p)
        subprocess.run(
            ["uv", "run", "ruff", "format", *sorted(touched)], cwd=cwd, capture_output=True
        )
        ok, log = run_checks(cwd)
        if dropped:
            log += f"\nОтброшены файлы вне списка: {dropped}"
        if ok:
            break
        messages += [
            {"role": "assistant", "content": answer},
            {
                "role": "user",
                "content": f"Проверки красные. Исправь только эти ошибки, в тех же файлах, тем же форматом:\n{log}",
            },
        ]
    stat = subprocess.run(["git", "diff", "--stat"], cwd=cwd, capture_output=True, text=True).stdout
    if ok and not a.no_commit and commit:
        subprocess.run(["git", "add", *sorted(touched)], cwd=cwd)
        subprocess.run(
            [
                "git",
                "commit",
                "-qm",
                commit[0],
                "-m",
                "Co-Authored-By: DeepSeek <noreply@deepseek.com>",
            ],
            cwd=cwd,
        )
    summary = (
        f"{'ЗЕЛЁНО' if ok else 'КРАСНО'} · кругов {rounds} · токенов {tokens} · {int(time.time() - t0)} с\n"
        f"файлы: {', '.join(sorted(touched)) or '—'}\n{stat}\n{log[-1500:]}"
    )
    (cwd / ".ds").mkdir(exist_ok=True)
    (cwd / ".ds" / "RESULT.md").write_text(summary)
    print(summary)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
