"""Самообучение на чужих ответах и цитатах под постом (ADR 0034, пп. 1–5, 7, 9).

Заглушки этапа R3 (красные тесты).
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.x.models import XPost

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "harvest.toml"
OUTCOMES_PATH = Path(__file__).resolve().parents[3] / "config" / "outcomes.toml"
ORIGIN = "reply_harvest"


@dataclass(frozen=True)
class SiblingConfig:
    hit_share: float = 0.05
    above_share: float = 0.01
    hit_top: int = 3
    hit_top_min_likes: int = 20


@dataclass(frozen=True)
class HarvestConfig:
    recheck_hours: int = 24
    fresh_hours: int = 24
    scan_max: int = 100
    scan_max_quotes: int = 40
    per_post: int = 20
    per_post_quotes: int = 5
    contrast: int = 5
    contrast_min_candidates: int = 10
    contrast_window_min: int = 120
    contrast_share: float = 0.10
    min_likes: int = 3
    min_chars: int = 25
    min_words: int = 4
    min_author_followers: int = 50
    min_latin_share: float = 0.8
    langs: tuple[str, ...] = ("en",)
    bot_handles: tuple[str, ...] = ()
    bot_phrases: tuple[str, ...] = ()
    spam_phrases: tuple[str, ...] = ()
    daily_max_materials: int = 500
    sibling: SiblingConfig = field(default_factory=SiblingConfig)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH, outcomes_path: Path = OUTCOMES_PATH) -> HarvestConfig:
        raw: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        sib = tomllib.loads(outcomes_path.read_text(encoding="utf-8")).get("sibling", {})
        for key in ("langs", "bot_handles", "bot_phrases", "spam_phrases"):
            raw[key] = tuple(raw.get(key, ()))
        return cls(**raw, sibling=SiblingConfig(**sib))


@dataclass(frozen=True)
class JunkCtx:
    own_handle: str | None
    parent_handle: str | None
    parent_author_id: str | None = None
    fresh: bool = False
    config: HarvestConfig = field(default_factory=HarvestConfig)


@dataclass(frozen=True)
class HarvestParent:
    post_id: str
    likes: int = 0
    posted_at: datetime | None = None


@dataclass(frozen=True)
class Selection:
    top: list[XPost]
    contrast: list[XPost]


@dataclass
class HarvestResult:
    status: str = "done"
    stored: int = 0
    refreshed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    recheck_at: datetime | None = None
    parent_id: UUID | None = None


class RepliesReader(Protocol):
    async def fetch_replies(self, post_id: str, limit: int = 30) -> list[XPost]: ...

    async def fetch_quotes(self, post_id: str, limit: int = 30) -> list[XPost]: ...


def junk_reason(post: XPost, ctx: JunkCtx) -> str | None:
    raise NotImplementedError


def select(
    replies: Sequence[XPost],
    parent: HarvestParent,
    config: HarvestConfig | None = None,
    *,
    limit: int | None = None,
    contrast: int | None = None,
) -> Selection:
    raise NotImplementedError


def sibling_tier(
    likes: int,
    parent_likes: int,
    rank: int,
    *,
    contrast: bool = False,
    config: SiblingConfig | None = None,
) -> str:
    raise NotImplementedError


def harvest_payload(post: XPost, url: str | None) -> dict[str, Any]:
    raise NotImplementedError


class Harvester:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        reader: RepliesReader,
        *,
        config: HarvestConfig | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._reader = reader
        self._cfg = config or HarvestConfig.load()

    async def run(self, account_id: UUID, payload: dict[str, Any]) -> HarvestResult:
        raise NotImplementedError

    async def recheck(self, account_id: UUID, payload: dict[str, Any]) -> HarvestResult:
        raise NotImplementedError
