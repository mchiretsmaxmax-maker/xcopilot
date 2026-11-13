"""Логи (заглушка до зелёного)."""
from typing import Any


def setup_logging(level: str) -> None:
    raise NotImplementedError


def mask_secrets(event_dict: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError
