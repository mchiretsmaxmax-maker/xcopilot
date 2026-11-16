"""Эксплуатационные файлы: runbook, compose, CI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_runbook_has_no_bare_alembic_command() -> None:
    assert "app alembic " not in (ROOT / "docs/runbook.md").read_text()


def test_compose_files_pin_app_hostname() -> None:
    for name in ("compose.yaml", "compose.prod.yaml"):
        data: dict[str, Any] = yaml.safe_load((ROOT / name).read_text())
        assert data["services"]["app"].get("hostname") == "xcopilot-app", name


def test_dev_compose_has_data_volume() -> None:
    data: dict[str, Any] = yaml.safe_load((ROOT / "compose.yaml").read_text())
    app = data["services"]["app"]
    assert "appdata:/app/data" in app["volumes"]
    assert app["environment"]["DATA_DIR"] == "/app/data"
    assert app["environment"]["FASTEMBED_CACHE_PATH"] == "/app/data/fastembed"
    assert "appdata" in data["volumes"]
