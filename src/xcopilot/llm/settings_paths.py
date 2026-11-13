"""Где лежат TOML-конфиги: $XCOPILOT_CONFIG_DIR или ./config (в контейнере — /app/config)."""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("XCOPILOT_CONFIG_DIR", "") or Path.cwd() / "config")
