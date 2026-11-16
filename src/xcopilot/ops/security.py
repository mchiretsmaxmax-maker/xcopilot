"""Проверки безопасности развёртывания (этап 15, задача 8; ADR 0028).

`compose_problems(config)` — статическая проверка прод-конфигурации Compose (словарь из YAML
или `docker compose config --format json`): нет опубликованных портов, у сервисов есть
healthcheck, политика рестарта, ротация логов и лимиты, БД только во внутренней сети,
приложение не под root и без повышения привилегий. `env_file_problem(path)` — права `.env`.

Запуск на сервере (так делает scripts/smoke.sh):
    docker compose -f compose.prod.yaml config --format json | python -m xcopilot.ops.security
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import Any

APP = "app"
DB = "db"
ROOT_USERS = {"root", "0", "0:0"}


def _service_problems(name: str, svc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for port in svc.get("ports") or []:
        problems.append(f"{name}: опубликован порт {port}")
    if not (svc.get("healthcheck") or {}).get("test"):
        problems.append(f"{name}: нет healthcheck")
    if svc.get("restart") not in ("unless-stopped", "always"):
        problems.append(f"{name}: нет политики рестарта unless-stopped/always")
    if not ((svc.get("logging") or {}).get("options") or {}).get("max-size"):
        problems.append(f"{name}: нет ротации логов (logging.options.max-size)")
    limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
    if not (limits.get("memory") and limits.get("cpus")):
        problems.append(f"{name}: нет лимитов памяти и CPU (deploy.resources.limits)")
    if "no-new-privileges:true" not in (svc.get("security_opt") or []):
        problems.append(f"{name}: нет security_opt no-new-privileges:true")
    return problems


def compose_problems(compose: dict[str, Any]) -> list[str]:
    """Список нарушений прод-конфигурации; пустой — всё в порядке."""
    services: dict[str, Any] = compose.get("services") or {}
    networks: dict[str, Any] = compose.get("networks") or {}
    problems: list[str] = []
    for name in (APP, DB):
        if name not in services:
            problems.append(f"нет сервиса {name}")
    for name, svc in services.items():
        problems += _service_problems(name, svc or {})
    if DB in services:
        db_nets = services[DB].get("networks") or []
        names = list(db_nets) if isinstance(db_nets, (list, dict)) else []
        if not names:
            problems.append("db: не указаны сети (нужна внутренняя)")
        for net in names:
            if (networks.get(net) or {}).get("internal") is not True:
                problems.append(f"db: сеть {net} не internal — БД видна снаружи")
    if APP in services:
        app = services[APP]
        if str(app.get("user", "root")) in ROOT_USERS:
            problems.append("app: запущен под root (нужен user: 10001:10001)")
    return problems


def env_file_problem(path: Path) -> str | None:
    """Права .env должны быть 600 (читает и пишет только владелец)."""
    if not path.exists():
        return f"{path}: файла нет"
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return f"{path}: права {mode:o}, нужны 600 (chmod 600 {path.name})"
    return None


def main(argv: list[str] | None = None) -> int:
    """Проверить compose из stdin (JSON или YAML) и, если указан, файл .env; код 1 — нарушения."""
    import yaml

    args = sys.argv[1:] if argv is None else argv
    problems = compose_problems(yaml.safe_load(sys.stdin.read()) or {})
    if args:
        env_problem = env_file_problem(Path(args[0]))
        if env_problem:
            problems.append(env_problem)
    for p in problems:
        print(f"✗ {p}")
    if not problems:
        print("✓ прод-конфигурация и .env в порядке")
    return 1 if problems else 0


__all__ = ["compose_problems", "env_file_problem", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
