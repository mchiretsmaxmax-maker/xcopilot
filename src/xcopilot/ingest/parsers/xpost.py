"""Парсер поста X (полученного через XService)."""

from __future__ import annotations

from xcopilot.ingest.parsers.base import ParsedDoc
from xcopilot.x.models import XPost


def parse(data: XPost) -> list[ParsedDoc]:
    raise NotImplementedError
