"""Совместимость указания с паспортом голоса (ТЗ v3.1, 9A, задача 7)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from xcopilot.brain.context import BrainContext


async def voice_compat(
    ctx: BrainContext, guidance: str, passport: Mapping[str, Any] | None
) -> float:
    """allowed × natural / 2 (0..1); без паспорта — 1.0 (нарушать нечего)."""
    raise NotImplementedError
