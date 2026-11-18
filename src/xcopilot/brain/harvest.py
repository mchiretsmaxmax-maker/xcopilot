"""Самообучение на чужих ответах и цитатах под постом (ADR 0034, пп. 1–5, 7, 9).

Владелец кидает пост в «💬 Ответы» (или «Пост → Ответ/Цитата») → задача
`brain.harvest_replies` сразу забирает лучшие ответы и цитаты под ним (FxEmbed), отсеивает
мусор кодом (`junk_reason`), берёт топ по лайкам и контраст (`select`) и пишет их обычными
материалами: `kind = reply | quote`, `stance = inspiration`, `origin = reply_harvest`,
`meta = {harvest, rank, contrast, lang, silent}`. Цифры — сигнал `public_metrics` с местом
среди соседей; исход — `basis = sibling_rank` (`sibling_tier`). Дальше обычный путь:
ingest.process → brain.analyze → детекторы и извлечение. Всё молча (`meta.silent`):
владелец видит только «🧠 Выучила…».

Свежий пост (моложе суток) через `recheck_hours` перепроверяется (`brain.harvest_recheck`):
новые лайки собранных ответов и добор новых до `per_post`. Потолок `daily_max_materials`
материалов за сутки: сверх него задача переносится на сутки, без сообщения.
"""

from __future__ import annotations

import re
import statistics
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import structlog

from xcopilot.brain import outcomes
from xcopilot.brain.signals import Signal, add_signal
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, AuthorsRepo, SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.repos.materials import MaterialOutcomesRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.ingest.dedupe import content_hash
from xcopilot.x.errors import XError, XNotFound
from xcopilot.x.models import XPost

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "harvest.toml"
OUTCOMES_PATH = Path(__file__).resolve().parents[3] / "config" / "outcomes.toml"
ORIGIN = "reply_harvest"
SETTING = "reply_learning"  # accounts.settings: выключатель сбора (по умолчанию включён)
HARVEST_KIND = "brain.harvest_replies"
RECHECK_KIND = "brain.harvest_recheck"
INGEST_KIND = "ingest.process"
MAX_ATTEMPTS = 3
_UNDETERMINED_LANGS = frozenset({"und", "zxx", "qme", "qht", "qam", "qct", "qst"})

log = structlog.get_logger()


@dataclass(frozen=True)
class SiblingConfig:
    """Пороги исхода по месту среди соседей (`config/outcomes.toml [sibling]`)."""

    hit_share: float = 0.05
    above_share: float = 0.01
    hit_top: int = 3
    hit_top_min_likes: int = 20

    @classmethod
    def load(cls, path: Path = OUTCOMES_PATH) -> SiblingConfig:
        raw = tomllib.loads(path.read_text(encoding="utf-8")).get("sibling", {})
        return cls(**raw)


@dataclass(frozen=True)
class HarvestConfig:
    """`config/harvest.toml` (описание полей — там же)."""

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
        for key in ("langs", "bot_handles", "bot_phrases", "spam_phrases"):
            raw[key] = tuple(raw.get(key, ()))
        return cls(**raw, sibling=SiblingConfig.load(outcomes_path))


# --------------------------------------------------------------------------- фильтр


@dataclass(frozen=True)
class JunkCtx:
    """Что нужно фильтру: свой хэндл, автор поста, свежесть поста, конфиг."""

    own_handle: str | None
    parent_handle: str | None
    parent_author_id: str | None = None
    fresh: bool = False  # пост моложе fresh_hours: порога лайков нет
    config: HarvestConfig = field(default_factory=HarvestConfig)


_MENTION = re.compile(r"@(\w{1,30})")
_URL = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+\.(?:com|io|xyz|gg|ly|co|me|app|so|fi)/\S*", re.I)
_TICKER = re.compile(r"(?<![\w$])\$[A-Za-z][A-Za-z0-9]{1,9}\b")
_HASHTAG = re.compile(r"#\w+")
_KEEP = re.compile(r"[^\w\s.,!?;:'\"()\-–—/%&]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def _likes(post: XPost) -> int:
    return post.metrics.likes if post.metrics else 0


def _core(text: str) -> str:
    """Текст без @упоминаний, ссылок, хэштегов и эмодзи (пробелы схлопнуты)."""
    out = _URL.sub(" ", text)
    out = _MENTION.sub(" ", out)
    out = _HASHTAG.sub(" ", out)
    out = _KEEP.sub(" ", out)
    return _SPACES.sub(" ", out).strip()


def _latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isascii() for c in letters) / len(letters)


def junk_reason(post: XPost, ctx: JunkCtx) -> str | None:
    """Причина не брать ответ (self, bot_call, promo, empty, short, lang, low_author,
    low_likes) или None — ответ годится. Только код, без модели (ADR 0034, п. 3)."""
    cfg = ctx.config
    handle = (post.author_handle or "").lower()
    selves = {h.lower().lstrip("@") for h in (ctx.own_handle, ctx.parent_handle) if h}
    if (handle and handle in selves) or (
        ctx.parent_author_id is not None and post.author_id == ctx.parent_author_id
    ):
        return "self"
    text = post.text
    low = text.lower()
    bots = {b.lower() for b in cfg.bot_handles}
    if any(m.lower() in bots for m in _MENTION.findall(text)) or any(
        p in low for p in cfg.bot_phrases
    ):
        return "bot_call"
    if _URL.search(text) or _TICKER.search(text) or any(p in low for p in cfg.spam_phrases):
        return "promo"
    core = _core(text)
    if not any(c.isalnum() for c in core):
        return "empty"
    words = [w for w in core.split() if any(c.isalnum() for c in w)]
    if len(core) < cfg.min_chars or len(words) < cfg.min_words:
        return "short"
    lang = (post.lang or "").lower() or None
    if lang in _UNDETERMINED_LANGS:
        lang = None
    if lang is not None:
        if cfg.langs and lang not in cfg.langs:
            return "lang"
    elif cfg.langs == ("en",) and _latin_share(core) < cfg.min_latin_share:
        return "lang"
    if post.author_followers is not None and post.author_followers < cfg.min_author_followers:
        return "low_author"
    if not ctx.fresh and _likes(post) < cfg.min_likes:
        return "low_likes"
    return None


# --------------------------------------------------------------------------- отбор


@dataclass(frozen=True)
class HarvestParent:
    """Исходный пост: id, лайки и время публикации (окно контраста)."""

    post_id: str
    likes: int = 0
    posted_at: datetime | None = None


@dataclass(frozen=True)
class Selection:
    top: list[XPost]
    contrast: list[XPost]


def normalize_text(text: str) -> str:
    """Ключ копипасты: нижний регистр, только буквы и цифры через пробел."""
    return " ".join(re.findall(r"\w+", _core(text).lower()))


def select(
    replies: Sequence[XPost],
    parent: HarvestParent,
    config: HarvestConfig | None = None,
    *,
    limit: int | None = None,
    contrast: int | None = None,
) -> Selection:
    """Топ `limit` (по умолчанию per_post) по лайкам, копипаста — один раз; контраст — до
    `contrast` ответов из первых contrast_window_min минут с лайками ≤ contrast_share
    медианы топа (иначе «поздний = слабый» стал бы ложным знанием)."""
    cfg = config or HarvestConfig.load()
    limit = cfg.per_post if limit is None else limit
    n_contrast = cfg.contrast if contrast is None else contrast
    seen: set[str] = set()
    uniq: list[XPost] = []
    for post in sorted(replies, key=lambda p: (-_likes(p), p.id)):
        key = normalize_text(post.text)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(post)
    top = uniq[: max(limit, 0)]
    rest = uniq[len(top) :]
    weak: list[XPost] = []
    if n_contrast > 0 and top:
        cap = statistics.median(_likes(p) for p in top) * cfg.contrast_share
        start = parent.posted_at or min(
            (p.created_at for p in top if p.created_at is not None), default=None
        )
        window = timedelta(minutes=cfg.contrast_window_min)
        if start is not None:
            weak = [
                p
                for p in rest
                if _likes(p) <= cap and p.created_at is not None and p.created_at - start <= window
            ]
            weak.sort(key=lambda p: (_likes(p), p.id))
    return Selection(top=top, contrast=weak[:n_contrast])


def sibling_tier(
    likes: int,
    parent_likes: int,
    rank: int,
    *,
    contrast: bool = False,
    config: SiblingConfig | None = None,
) -> str:
    """Исход ответа по месту среди соседей (basis sibling_rank): hit | above | normal | flop."""
    if contrast:
        return "flop"
    cfg = config or SiblingConfig.load()
    share = likes / max(parent_likes, 1)
    if share >= cfg.hit_share or (rank <= cfg.hit_top and likes >= cfg.hit_top_min_likes):
        return "hit"
    if share >= cfg.above_share:
        return "above"
    return "normal"


# --------------------------------------------------------------------------- задача


def harvest_payload(post: XPost, url: str | None) -> dict[str, Any]:
    """Payload задачи сбора по прочитанному посту (ADR 0034, п. 1.1)."""
    m = post.metrics
    return {
        "post_id": post.id,
        "url": url,
        "handle": post.author_handle,
        "author_id": post.author_id,
        "text": post.text,
        "lang": post.lang,
        "posted_at": post.created_at.isoformat() if post.created_at else None,
        "parent_metrics": {
            "likes": m.likes if m else 0,
            "replies": m.replies if m else 0,
            "reposts": m.reposts if m else 0,
            "views": m.impressions if m else None,
        },
    }


@dataclass
class HarvestResult:
    status: str = "done"  # done | capped | off | not_found
    stored: int = 0
    refreshed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    recheck_at: datetime | None = None
    parent_id: UUID | None = None


@dataclass(frozen=True)
class PickResult:
    """Итог автосверки ответа владельца с вариантами: approved | edited | no_match |
    no_candidates | expired."""

    status: str
    draft_id: UUID | None = None
    similarity: float | None = None


@runtime_checkable
class RepliesReader(Protocol):
    async def fetch_replies(self, post_id: str, limit: int = 30) -> list[XPost]: ...

    async def fetch_quotes(self, post_id: str, limit: int = 30) -> list[XPost]: ...


@dataclass(frozen=True)
class _Item:
    post: XPost
    kind: str  # reply | quote
    rank: int
    contrast: bool = False


def _posted_at(payload: dict[str, Any]) -> datetime | None:
    raw = payload.get("posted_at")
    return datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None


def reply_learning_on(settings: dict[str, Any] | None) -> bool:
    return bool((settings or {}).get(SETTING, True))


async def enqueue_harvest_job(
    session: DbSession,
    account_id: UUID,
    payload: dict[str, Any],
    *,
    kind: str = HARVEST_KIND,
    run_at: datetime,
    key_suffix: str = "",
) -> UUID:
    return await JobsRepo(session).enqueue(
        account_id=account_id,
        kind=kind,
        payload=payload,
        run_at=run_at,
        max_attempts=MAX_ATTEMPTS,
        idempotency_key=f"{kind}:{payload['post_id']}{key_suffix}",
    )


class Harvester:
    """Сбор ответов и цитат под постом в материалы `reply_harvest` (без сообщений владельцу)."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        reader: RepliesReader,
        *,
        config: HarvestConfig | None = None,
        decisions: Any | None = None,
    ) -> None:
        self._decisions = decisions
        self._uow = uow_factory
        self._clock = clock
        self._reader = reader
        self._cfg = config or HarvestConfig.load()

    async def run(self, account_id: UUID, payload: dict[str, Any]) -> HarvestResult:
        """Первый сбор: всё, что есть сейчас; свежему посту — перепроверка через сутки."""
        return await self._harvest(account_id, payload, recheck=False)

    async def recheck(self, account_id: UUID, payload: dict[str, Any]) -> HarvestResult:
        """Перепроверка: новые лайки собранных ответов и добор новых до per_post."""
        return await self._harvest(account_id, payload, recheck=True)

    async def match_owner_reply(
        self,
        account_id: UUID,
        post_id: str,
        owner_reply: XPost,
        *,
        payload: dict[str, Any] | None = None,
    ) -> PickResult:
        """Ответ владельца под постом: сохранить как own/reply и сверить с вариантами (R4)."""
        raise NotImplementedError

    async def _harvest(
        self, account_id: UUID, payload: dict[str, Any], *, recheck: bool
    ) -> HarvestResult:
        cfg = self._cfg
        now = self._clock.now()
        post_id = str(payload["post_id"])
        posted_at = _posted_at(payload)
        fresh = posted_at is not None and now - posted_at < timedelta(hours=cfg.fresh_hours)
        result = HarvestResult()
        async with self._uow() as uow:
            account = await AccountsRepo(uow.session).get(account_id)
            if account is None or not reply_learning_on(account.settings):
                return HarvestResult(status="off")
            own_handle = account.handle
            used = await SourcesRepo(uow.session, account_id).count_harvest_since(
                ORIGIN, now - timedelta(days=1)
            )
        remaining = cfg.daily_max_materials - used
        if remaining <= 0:
            await self._reschedule(account_id, payload, now, recheck=recheck)
            log.info("harvest.capped", post_id=post_id, used=used)
            return HarvestResult(status="capped")
        try:
            replies = await self._reader.fetch_replies(post_id, limit=cfg.scan_max)
        except XNotFound:
            log.info("harvest.post_gone", post_id=post_id)
            return HarvestResult(status="not_found")
        try:
            quotes = await self._reader.fetch_quotes(post_id, limit=cfg.scan_max_quotes)
        except XError as exc:
            log.info("harvest.quotes_failed", post_id=post_id, error=type(exc).__name__)
            quotes = []

        ctx = JunkCtx(
            own_handle=own_handle,
            parent_handle=payload.get("handle"),
            parent_author_id=payload.get("author_id"),
            fresh=fresh,
            config=cfg,
        )
        parent = HarvestParent(
            post_id=post_id,
            likes=int((payload.get("parent_metrics") or {}).get("likes") or 0),
            posted_at=posted_at,
        )
        skipped: Counter[str] = Counter()
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            mine = await sources.harvested(ORIGIN, post_id)
            fetched = {p.id: ("reply", p) for p in replies} | {p.id: ("quote", p) for p in quotes}
            new_replies: list[XPost] = []
            new_quotes: list[XPost] = []
            good: dict[str, list[XPost]] = {"reply": [], "quote": []}
            for kind, post in fetched.values():
                existing = mine.get(post.id) or await sources.by_external_id(post.id)
                if existing is not None:
                    await self._refresh(session, account_id, existing, post, parent, now)
                    result.refreshed += 1
                    if existing.id in {s.id for s in mine.values()}:
                        good[kind].append(post)
                    continue
                reason = junk_reason(post, ctx)
                if reason is not None:
                    skipped[reason] += 1
                    continue
                good[kind].append(post)
                (new_replies if kind == "reply" else new_quotes).append(post)

            ranks = {
                kind: {p.id: i for i, p in enumerate(_by_likes(posts), 1)}
                for kind, posts in good.items()
            }
            have = Counter(
                ("contrast" if s.meta.get("contrast") else s.kind) for s in mine.values()
            )
            n_contrast = max(cfg.contrast - have["contrast"], 0)
            if not recheck and fresh and len(new_replies) < cfg.contrast_min_candidates:
                n_contrast = 0  # контраст появится при перепроверке
            sel = select(
                new_replies,
                parent,
                cfg,
                limit=max(cfg.per_post - have["reply"], 0),
                contrast=n_contrast,
            )
            qsel = select(
                new_quotes,
                parent,
                cfg,
                limit=max(cfg.per_post_quotes - have["quote"], 0),
                contrast=0,
            )
            items = [_Item(p, "reply", ranks["reply"][p.id]) for p in sel.top]
            items += [_Item(p, "quote", ranks["quote"][p.id]) for p in qsel.top]
            items += [_Item(p, "reply", ranks["reply"][p.id], contrast=True) for p in sel.contrast]
            capped = len(items) > remaining
            items = items[:remaining]
            if items:
                parent_id = await self._parent(session, account_id, payload, now)
                result.parent_id = parent_id
                authors = AuthorsRepo(session, account_id)
                for item in items:
                    if await self._store(
                        session, sources, authors, account_id, item, parent, parent_id, now
                    ):
                        result.stored += 1
                    else:
                        skipped["duplicate"] += 1
            if not recheck and fresh and posted_at is not None:
                due = now + timedelta(hours=cfg.recheck_hours)
                await enqueue_harvest_job(
                    session, account_id, payload, kind=RECHECK_KIND, run_at=due
                )
                result.recheck_at = due
        if capped:
            await self._reschedule(account_id, payload, now, recheck=recheck)
            result.status = "capped"
        result.skipped = dict(skipped)
        log.info(
            "harvest.done",
            post_id=post_id,
            recheck=recheck,
            stored=result.stored,
            refreshed=result.refreshed,
            skipped=result.skipped,
        )
        return result

    async def _reschedule(
        self, account_id: UUID, payload: dict[str, Any], now: datetime, *, recheck: bool
    ) -> None:
        """Потолок за сутки: та же задача через сутки (новый ключ — на дату)."""
        async with self._uow() as uow:
            await enqueue_harvest_job(
                uow.session,
                account_id,
                payload,
                kind=RECHECK_KIND if recheck else HARVEST_KIND,
                run_at=now + timedelta(days=1),
                key_suffix=f":{(now + timedelta(days=1)):%Y%m%d}",
            )

    async def _parent(
        self, session: DbSession, account_id: UUID, payload: dict[str, Any], now: datetime
    ) -> UUID:
        """Исходный пост: уже есть (свой `own` или прежний сбор) — он; иначе `reference`."""
        post_id = str(payload["post_id"])
        sources = SourcesRepo(session, account_id)
        found = await sources.by_external_id(post_id)
        if found is not None:
            return found.id
        text = str(payload.get("text") or payload.get("url") or post_id)
        handle = payload.get("handle")
        norm = str(handle).lstrip("@").lower() if handle else None
        author_id = await _author(AuthorsRepo(session, account_id), norm)
        src, new = await sources.add_or_get(
            kind="post",
            stance="reference",
            origin=ORIGIN,
            content_hash=content_hash(text),
            external_id=post_id,
            url=payload.get("url"),
            author_handle=norm,
            author_id=author_id,
            posted_at=_posted_at(payload),
            meta={"harvest_parent": post_id, "silent": True},
        )
        if new:
            await sources.add_document(src.id, text=text)
            metrics = payload.get("parent_metrics") or {}
            await add_signal(
                session,
                account_id,
                src.id,
                Signal(
                    origin=outcomes.AUTO_ORIGIN,
                    views=metrics.get("views"),
                    likes=metrics.get("likes"),
                    replies=metrics.get("replies"),
                    reposts=metrics.get("reposts"),
                    raw={"via": "fxembed_status"},
                ),
                observed_at=now,
            )
            await _enqueue_ingest(session, account_id, src.id, now)
        return src.id

    async def _store(
        self,
        session: DbSession,
        sources: SourcesRepo,
        authors: AuthorsRepo,
        account_id: UUID,
        item: _Item,
        parent: HarvestParent,
        parent_id: UUID,
        now: datetime,
    ) -> bool:
        post = item.post
        handle = (post.author_handle or "").lstrip("@").lower() or None
        src, new = await sources.add_or_get(
            kind=item.kind,
            stance="inspiration",
            origin=ORIGIN,
            content_hash=content_hash(post.text),
            external_id=post.id,
            url=f"https://x.com/{handle or 'i/web'}/status/{post.id}",
            author_handle=handle,
            author_id=await _author(authors, handle),
            posted_at=post.created_at,
            parent_source_id=parent_id,
            meta={
                "harvest": parent.post_id,
                "rank": item.rank,
                "contrast": item.contrast,
                "lang": post.lang,
                "silent": True,
            },
        )
        if not new:
            return False
        await sources.add_document(src.id, text=post.text)
        await add_signal(
            session,
            account_id,
            src.id,
            _signal(post, item.kind, parent, item.rank, item.contrast, now),
            observed_at=now,
        )
        await _enqueue_ingest(session, account_id, src.id, now)
        return True

    async def _refresh(
        self,
        session: DbSession,
        account_id: UUID,
        src: Any,
        post: XPost,
        parent: HarvestParent,
        now: datetime,
    ) -> None:
        """Уже собранный (или присланный владельцем) ответ: новый сигнал цифр и пересчёт исхода.

        Исход изменился — compute_outcome сам ставит brain.detect с revise.
        """
        meta = src.meta or {}
        rank = int(meta.get("rank") or 0)
        kind = src.kind if src.kind in ("reply", "quote") else "reply"
        signal = _signal(post, kind, parent, rank, bool(meta.get("contrast")), now)
        await add_signal(session, account_id, src.id, signal, observed_at=now)
        if await MaterialOutcomesRepo(session, account_id).current(src.id) is not None:
            await outcomes.compute_outcome(session, account_id, src.id, now=now)


def _by_likes(posts: Sequence[XPost]) -> list[XPost]:
    return sorted(posts, key=lambda p: (-_likes(p), p.id))


def _signal(
    post: XPost, kind: str, parent: HarvestParent, rank: int, contrast: bool, now: datetime
) -> Signal:
    m = post.metrics
    minutes = (
        round((post.created_at - parent.posted_at).total_seconds() / 60)
        if post.created_at is not None and parent.posted_at is not None
        else None
    )
    age = (
        max(0.0, (now - post.created_at).total_seconds() / 3600)
        if post.created_at is not None
        else None
    )
    return Signal(
        origin=outcomes.AUTO_ORIGIN,
        views=m.impressions if m else None,
        likes=m.likes if m else 0,
        replies=m.replies if m else None,
        reposts=m.reposts if m else None,
        quotes=m.quotes if m else None,
        bookmarks=m.bookmarks if m else None,
        author_followers=post.author_followers,
        post_age_hours=age,
        raw={
            "via": "fxembed_conversation" if kind == "reply" else "fxembed_quotes",
            "parent_likes": parent.likes,
            "rank": rank,
            "contrast": contrast,
            "minutes_after_post": minutes,
        },
    )


async def _author(authors: AuthorsRepo, handle: str | None) -> UUID | None:
    if not handle:
        return None
    try:
        return (await authors.get_or_create(handle)).id
    except ValueError:
        return None


async def _enqueue_ingest(
    session: DbSession, account_id: UUID, source_id: UUID, now: datetime
) -> None:
    await JobsRepo(session).enqueue(
        account_id=account_id,
        kind=INGEST_KIND,
        payload={"source_id": str(source_id), "silent": True},
        run_at=now,
        max_attempts=5,
        idempotency_key=f"ingest:{source_id}",
    )


async def status_line(session: DbSession, account_id: UUID) -> str | None:
    """Строка /status: «Из ответов: 312 примеров с 14 постов, приёмов: 5 (2 на испытании)»."""
    sources = SourcesRepo(session, account_id)
    total, posts = await sources.harvest_stats(ORIGIN)
    if not total:
        return None
    by_status = await sources.format_knowledge_counts(("reply", "quote"))
    skills = sum(by_status.values())
    trial = by_status.get("probation", 0)
    tail = f" ({trial} на испытании)" if trial else ""
    return f"Из ответов: {total} примеров с {posts} постов, приёмов: {skills}{tail}"
