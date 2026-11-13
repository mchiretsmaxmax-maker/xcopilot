"""Сборка настоящих провайдеров и шлюза из настроек."""

from __future__ import annotations

from xcopilot.config import Settings
from xcopilot.llm.providers.base import DecisionProvider, TextProvider


def build_providers(settings: Settings) -> dict[str, TextProvider | DecisionProvider]:
    raise NotImplementedError
