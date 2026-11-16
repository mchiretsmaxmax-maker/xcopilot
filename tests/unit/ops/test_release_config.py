"""Этап 15: прод-конфигурация, шаблон .env и файл baseline проверяются статически."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from tests.evals.run_evals import BASELINE_PATH, validate_baseline
from xcopilot.config import Settings
from xcopilot.ops.security import compose_problems

ROOT = Path(__file__).resolve().parents[3]


def _prod() -> dict[str, Any]:
    data = yaml.safe_load((ROOT / "compose.prod.yaml").read_text())
    assert isinstance(data, dict)
    return data


def test_prod_compose_has_no_published_ports_and_has_healthchecks() -> None:
    prod = _prod()
    services: dict[str, Any] = prod["services"]
    assert {"app", "db"} <= set(services)
    for name, svc in services.items():
        assert "ports" not in svc, f"{name}: порт опубликован наружу"
        assert "healthcheck" in svc and svc["healthcheck"].get("test"), f"{name}: нет healthcheck"
        assert svc.get("restart") in ("unless-stopped", "always"), f"{name}: нет restart"
        logging = svc.get("logging", {})
        assert logging.get("options", {}).get("max-size"), f"{name}: нет ротации логов"
        limits = svc.get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory") and limits.get("cpus"), f"{name}: нет лимитов ресурсов"
    # БД — только во внутренней сети, без выхода наружу
    db_nets = services["db"]["networks"]
    assert db_nets and all(prod["networks"][n].get("internal") is True for n in db_nets)
    app = services["app"]
    assert app.get("user") not in (None, "root", "0", "0:0")  # непривилегированный пользователь
    assert "no-new-privileges:true" in app.get("security_opt", [])
    assert app.get("env_file") in (".env", [".env"])
    assert "pgdata" in prod["volumes"]
    # та же проверка, что выполняет scripts/smoke.sh на сервере
    assert compose_problems(prod) == []
    bad = {"services": {"db": {**services["db"], "ports": ["5432:5432"]}}}
    assert any("5432" in p for p in compose_problems(bad))


def test_env_example_covers_all_settings() -> None:
    example = (ROOT / ".env.example").read_text()
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", example, flags=re.MULTILINE))
    missing = sorted(name.upper() for name in Settings.model_fields if name.upper() not in keys)
    assert missing == []
    # переменные бэкапа и прод-развёртывания (не Settings, читаются скриптами)
    for key in ("BACKUP_DIR", "BACKUP_PASSPHRASE_FILE", "BACKUP_REMOTE", "BACKUP_KEEP_DAYS"):
        assert key in keys, key


def test_eval_baseline_file_exists_and_valid() -> None:
    assert BASELINE_PATH == ROOT / "tests/evals/baseline.json"
    data = json.loads(BASELINE_PATH.read_text())
    assert validate_baseline(data) == []
    # до этапа 15b замер не записан: pending и пустые числа; после — recorded и все метрики
    if data["status"] == "pending":
        assert all(v is None for v in data["metrics"].values())
        assert data["ablation"] is None
    else:
        assert data["status"] == "recorded"
        assert data["metrics"]["zero_edit_rate"] is not None
    # схема строгая: recorded без чисел или неизвестный статус — ошибка
    broken = {**data, "status": "recorded", "metrics": dict.fromkeys(data["metrics"])}
    assert validate_baseline(broken) != []
    assert validate_baseline({**data, "status": "draft"}) != []
    assert validate_baseline({k: v for k, v in data.items() if k != "metrics"}) != []
