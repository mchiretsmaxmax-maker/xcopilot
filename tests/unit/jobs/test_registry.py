"""Реестр обработчиков покрывает все виды задач, которые ставит код."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from xcopilot.jobs.handlers import KNOWN_KINDS, JobDeps, build_registry
from xcopilot.jobs.scheduler import DEFAULT_SCHEDULES

SRC = Path(__file__).resolve().parents[3] / "src" / "xcopilot"
# enqueue("kind", …) / kind="kind" / XXX_KIND = "kind" — литерал вида задачи
LITERALS = (
    re.compile(r"enqueue\(\s*\"([a-z_]+\.[a-z_]+)\""),
    re.compile(r"\bkind=\"([a-z_]+\.[a-z_]+)\""),
    re.compile(r"^\s*[A-Z_]*KIND\s*=\s*\"([a-z_]+\.[a-z_]+)\"", re.M),
)
SEEDED = re.compile(r"INSERT INTO schedules.*?VALUES \([^,]+, '([a-z_]+\.[a-z_]+)'", re.S)


def _enqueued_kinds() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        body = path.read_text()
        for rx in LITERALS:
            found |= set(rx.findall(body))
    return found


def _seeded_kinds() -> set[str]:
    found: set[str] = set()
    for path in (SRC / "db" / "migrations").rglob("*.py"):
        found |= set(SEEDED.findall(path.read_text()))
    return found


def _dummy_deps() -> JobDeps:
    nothing = cast(Any, object())
    return JobDeps(
        uow_factory=nothing,
        clock=nothing,
        queue=nothing,
        ingest=nothing,
        analyzer=nothing,
        importer=nothing,
        writer=nothing,
        topics=nothing,
        brain=nothing,
        x_for=nothing,
        notifier=nothing,
        bot=nothing,
    )


def test_scan_finds_known_producers() -> None:
    kinds = _enqueued_kinds()
    for kind in ("ingest.process", "brain.analyze", "writer.generate", "x.recheck_metrics"):
        assert kind in kinds
    assert "db.ensure_partitions" in _seeded_kinds()


def test_registry_covers_every_enqueued_kind() -> None:
    missing = (_enqueued_kinds() | _seeded_kinds()) - KNOWN_KINDS
    assert missing == set()


def test_registry_matches_known_kinds() -> None:
    registry = build_registry(_dummy_deps())
    assert set(registry) == KNOWN_KINDS
    assert {d.kind for d in DEFAULT_SCHEDULES} <= KNOWN_KINDS
