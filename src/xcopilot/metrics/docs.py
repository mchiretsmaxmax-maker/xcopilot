"""Генератор docs/db/metrics.md из каталога metrics/definitions."""

from __future__ import annotations

from pathlib import Path

from xcopilot.metrics.catalog import MetricDefinition


def render_metrics_md(definitions: list[MetricDefinition] | None = None) -> str:
    raise NotImplementedError


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    raise NotImplementedError
