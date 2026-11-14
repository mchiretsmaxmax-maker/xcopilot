"""IngestService: приём входа (accept) и обработка источника (process).

Статусы источника (схема этапа 2): new (в ТЗ pending) → parsed → indexed (в ТЗ embedded и
ready: куски, теги и векторы пишутся одной транзакцией) | failed | excluded (дубль или
недоступно по режиму X). Обработчик идемпотентен: завершённый источник не трогается,
сбой посередине оставляет предыдущий статус, и повтор задачи доделывает работу.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import structlog

from xcopilot.bot import keyboards, texts
from xcopilot.brain.schemas import ScreenshotRead
from xcopilot.clock import Clock
from xcopilot.db.repos import (
    AccountsRepo,
    AuthorsRepo,
    ChunksRepo,
    EmbeddingModelsRepo,
    MaterialSignalsRepo,
    SourcesRepo,
)
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.repos.sources import OWN_STANCE, material_kind_stance, source_material
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder, batched
from xcopilot.errors import ConfigError
from xcopilot.ingest.blobs import BlobStore
from xcopilot.ingest.chunker import chunk, count_tokens
from xcopilot.ingest.classify import Incoming, InputKind, article_urls, classify
from xcopilot.ingest.clean import normalize
from xcopilot.ingest.dedupe import content_hash, find_near_duplicate
from xcopilot.ingest.errors import NoTextLayer, ParseError
from xcopilot.ingest.parsers import SUPPORTED_EXTENSIONS, parse_file, xpost
from xcopilot.ingest.parsers.article import fetch_article as default_fetch_article
from xcopilot.ingest.parsers.base import MAX_FILE_BYTES, MAX_TEXT_CHARS, ParsedDoc
from xcopilot.ingest.parsers.screenshot import (
    COUNTERS,
    DisabledScreenshotReader,
    ScreenshotReader,
    ScreenshotUnavailable,
    alternatives,
    doubtful,
)
from xcopilot.ingest.screen import REASON_AUTHOR_BLOCKED, Screener
from xcopilot.ingest.scrub import scrub_pii
from xcopilot.ingest.tagger import Tagger, Tags
from xcopilot.llm.errors import LLMOutputError, PermanentProviderError
from xcopilot.metrics.events import EventType, emit
from xcopilot.x.client import XClient
from xcopilot.x.errors import XModeError, XNotFound
from xcopilot.x.models import PostRef
from xcopilot.x.urls import extract_post_urls, parse_handle, parse_post_url

log = structlog.get_logger()

INGEST_KIND = "ingest.process"
POST_KINDS = frozenset({"post", "reply", "quote", "repost"})  # не режутся на куски
PII_KINDS = frozenset({"file", "chat"})
ASK_KINDS = ("own_post", "liked_sample", "competitor_post", "note")
TERMINAL = frozenset({"indexed", "excluded", "failed"})
POST_MAX_CHARS = 25_000  # длинный пост X (Premium); длиннее — материал
OWN_POSTS_LIMIT = 50
_REPOST = re.compile(r"^RT @(\w{1,15}):\s*(.+)$", re.S)
SCREENSHOT_KINDS = {"post": "post", "reply": "reply", "quote": "quote", "thread_part": "thread"}
STANCES = ("own", "inspiration", "competitor", "anti_example", "reference")
_SEPARATOR = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|—{2,}|_{3,})[ \t]*$", re.M)


class NotifierLike(Protocol):
    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


XFactory = Callable[[UUID], Awaitable[XClient]]
ArticleFetcher = Callable[[str], Awaitable[ParsedDoc]]


@dataclass(frozen=True, slots=True)
class FilePayload:
    name: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ImagePayload:
    """Картинка (скриншот поста), уже скачанная из Telegram."""

    data: bytes
    mime: str = "image/png"


@dataclass(frozen=True, slots=True)
class ForwardInfo:
    """Откуда переслано сообщение Telegram: имя, username, название канала."""

    name: str | None = None
    username: str | None = None
    chat_title: str | None = None


@dataclass(frozen=True, slots=True)
class IngestPayload:
    """Вход от пользователя: текст, файл или картинки; owner_tg_id — для экспорта Telegram.

    ТЗ v3.1: `images` — скриншоты, `forward` — автор пересылки, `note` — подпись владельца.
    """

    text: str | None = None
    file: FilePayload | None = None
    file_name: str | None = None
    file_size: int | None = None
    owner_tg_id: int | None = None
    images: tuple[ImagePayload, ...] = ()
    forward: ForwardInfo | None = None
    note: str | None = None


@dataclass
class IngestReceipt:
    kind: InputKind
    message: str
    batch_id: UUID | None = None
    source_ids: list[UUID] = field(default_factory=list)
    duplicates: int = 0
    rejected: bool = False
    offer_paid: bool = False
    keyboard: Any = None


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: str
    chunks: int = 0
    duplicates: int = 0


@dataclass
class _Extra:
    """Что разбор узнал сверх текста: прочитанный скрин, репост (хэндл, текст исходного)."""

    screenshot: ScreenshotRead | None = None
    repost: tuple[str, str] | None = None


def _forward_meta(fwd: ForwardInfo) -> dict[str, str | None]:
    return {"name": fwd.name, "username": fwd.username, "chat_title": fwd.chat_title}


def _post_fields(updates: dict[str, Any]) -> dict[str, Any]:
    keys = ("external_id", "url", "author_handle", "origin")
    return {k: v for k, v in updates.items() if k in keys}


@dataclass
class _Piece:
    document_id: UUID
    idx: int
    text: str


class _Stop(Exception):
    """Источник закрыт без индексации (failed / excluded); message — владельцу (или None)."""

    def __init__(self, status: str, reason: str, message: str | None, keyboard: Any = None) -> None:
        super().__init__(reason)
        self.status, self.reason, self.message, self.keyboard = status, reason, message, keyboard


_COUNT_ORDER = ("links", "screenshots", "articles", "texts", "files")


@dataclass
class _AcceptCtx:
    """Общее для входов одной партии: репозитории, созданные источники, счётчики."""

    sources: SourcesRepo
    authors: AuthorsRepo
    own_handle: str
    read_mode: str
    meta: dict[str, Any]
    created: list[UUID] = field(default_factory=list)
    dups: int = 0
    tally: Counter[str] = field(default_factory=Counter)

    async def add(
        self, kind: str, stance: str, origin: str, chash: str, **fields: Any
    ) -> UUID | None:
        """Новый источник (id) или None, если такой уже есть (дубль)."""
        handle = fields.get("author_handle")
        if handle and "author_id" not in fields:
            fields["author_id"] = await _author_id(self.authors, handle, self.own_handle)
        src, new = await self.sources.add_or_get(
            kind=kind, stance=stance, origin=origin, content_hash=chash, **fields
        )
        if not new:
            self.dups += 1
            return None
        self.created.append(src.id)
        return src.id

    async def add_material(
        self, material: str, origin: str, chash: str, **fields: Any
    ) -> UUID | None:
        kind, stance = material_kind_stance(material)
        return await self.add(kind, stance, origin, chash, **fields)

    def counts(self) -> dict[str, int]:
        out = {key: self.tally[key] for key in _COUNT_ORDER if self.tally[key]}
        if self.dups:
            out["duplicates"] = self.dups
        return out


def split_posts(text: str) -> list[str]:
    """Несколько постов в одном сообщении разделяются строкой `---` (или `***`)."""
    return [p.strip() for p in _SEPARATOR.split(text) if p.strip()]


def file_precheck(name: str, size: int | None) -> str | None:
    """Сообщение об отказе для файла (размер, формат) или None, если файл можно брать."""
    if size is not None and size > MAX_FILE_BYTES:
        return texts.INGEST_FILE_TOO_BIG
    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return texts.INGEST_UNSUPPORTED
    return None


class IngestService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        tagger: Tagger,
        embedder: Embedder,
        x_for: XFactory,
        notifier: NotifierLike,
        blobs: BlobStore,
        max_attempts: int = 5,
        screenshots: ScreenshotReader | None = None,
        screener: Screener | None = None,
        fetch_article: ArticleFetcher | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._tagger = tagger
        self._embedder = embedder
        self._x_for = x_for
        self._notifier = notifier
        self._blobs = blobs
        self._max_attempts = max_attempts
        self._screenshots: ScreenshotReader = screenshots or DisabledScreenshotReader()
        self._screener = screener
        self._fetch_article: ArticleFetcher = fetch_article or default_fetch_article

    # --- приём ------------------------------------------------------------------------------

    async def accept(
        self, account_id: UUID, user_id: UUID, payload: IngestPayload
    ) -> IngestReceipt:
        """Принять вход: создать источники (без дублей) и поставить задачи ingest.process."""
        return await self.accept_batch(account_id, user_id, [payload])

    async def accept_batch(
        self, account_id: UUID, user_id: UUID, payloads: Sequence[IngestPayload]
    ) -> IngestReceipt:
        """Пачка входов (альбом, серия сообщений) → одна партия и одна сводка.

        Один вход — прежние ответы («Принял 3 поста…»); несколько — «Принял 20 материалов:
        14 ссылок, 6 скринов…». Отказы (размер, формат) в пачке пропускаются.
        """
        if not payloads:
            raise ValueError("accept_batch: пустая пачка")
        prepared: list[tuple[IngestPayload, InputKind, str]] = []
        for payload in payloads:
            kind, refusal = self._precheck(payload)
            if refusal is not None:
                if len(payloads) == 1:
                    return IngestReceipt(kind, refusal, rejected=True)
                continue
            prepared.append((payload, kind, (payload.text or "").strip()))
        if not prepared:
            return IngestReceipt(InputKind.EMPTY, texts.INGEST_EMPTY, rejected=True)

        batch = uuid.uuid4()
        multi = len(payloads) > 1
        async with self._uow() as uow:
            session = uow.session
            account = await AccountsRepo(session).get(account_id)
            if account is None:
                raise ConfigError(f"аккаунт {account_id} не найден")
            ctx = _AcceptCtx(
                sources=SourcesRepo(session, account_id),
                authors=AuthorsRepo(session, account_id),
                own_handle=account.handle.lower(),
                read_mode=account.x_read_mode,
                meta={"batch": str(batch), "accepted_by": str(user_id)}
                | ({"multi": True} if multi else {}),
            )
            receipts = [await self._accept_one(ctx, p, kind, text) for p, kind, text in prepared]
            jobs = JobsRepo(session)
            for src_id in ctx.created:
                await ctx.sources.merge_meta(src_id, batch_dups=ctx.dups)
                await self._enqueue(jobs, account_id, src_id)
            input_kind = receipts[0].kind if len(receipts) == 1 else InputKind.BATCH
            await emit(
                session,
                account_id=account_id,
                type=EventType.INGEST_ACCEPTED,
                actor="user",
                user_id=user_id,
                props={
                    "input": input_kind.value,
                    "sources": len(ctx.created),
                    "duplicates": ctx.dups,
                },
            )
        if len(receipts) == 1:
            receipt = receipts[0]
        elif ctx.created:
            receipt = IngestReceipt(InputKind.BATCH, texts.ingest_accepted_batch(ctx.counts()))
        else:
            receipt = IngestReceipt(InputKind.BATCH, texts.INGEST_ALL_DUPLICATES)
        receipt.batch_id = batch if ctx.created else None
        receipt.source_ids = ctx.created
        receipt.duplicates = ctx.dups
        return receipt

    @staticmethod
    def _precheck(payload: IngestPayload) -> tuple[InputKind, str | None]:
        """Вид входа и отказ (или None) до записи в БД."""
        if payload.images:
            return InputKind.SCREENSHOT, None
        name = payload.file_name or (payload.file.name if payload.file else None)
        size = payload.file_size
        if size is None and payload.file is not None:
            size = len(payload.file.data)
        kind = classify(Incoming(text=payload.text, file_name=name, file_size=size))
        if kind == InputKind.EMPTY:
            return kind, texts.INGEST_EMPTY
        if kind == InputKind.FILE:
            assert name is not None
            refusal = file_precheck(name, size)
            if refusal is not None:
                return kind, refusal
            if payload.file is None:
                raise ValueError("accept: файл нужно скачать до приёма")
        elif len((payload.text or "").strip()) > MAX_TEXT_CHARS:
            return kind, texts.INGEST_TEXT_TOO_LONG
        return kind, None

    async def _enqueue(self, jobs: JobsRepo, account_id: UUID, src_id: UUID, key: str = "") -> None:
        await jobs.enqueue(
            account_id=account_id,
            kind=INGEST_KIND,
            payload={"source_id": str(src_id)},
            run_at=self._clock.now(),
            max_attempts=self._max_attempts,
            idempotency_key=f"ingest:{src_id}{key}",
        )

    async def _accept_one(
        self, ctx: _AcceptCtx, payload: IngestPayload, kind: InputKind, text: str
    ) -> IngestReceipt:
        """Источники одного входа; вернуть ответ так, будто вход пришёл один."""
        before = len(ctx.created)
        meta = dict(ctx.meta)
        if payload.forward is not None and kind == InputKind.TEXT:
            return await self._accept_forward(ctx, payload, text, meta)
        if payload.forward is not None:
            meta["forward_from"] = _forward_meta(payload.forward)
        if kind == InputKind.SCREENSHOT:
            for image in payload.images:
                src_id = await ctx.add(
                    "post",
                    "inspiration",
                    "screenshot",
                    _bytes_hash(image.data, "img"),
                    owner_note=payload.note,
                    meta={**meta, "image_mime": image.mime},
                )
                if src_id is not None:
                    await self._blobs.put(str(src_id), image.data)
                    ctx.tally["screenshots"] += 1
            n = len(ctx.created) - before
            message = texts.ingest_accepted_batch({"screenshots": n}) if n else ""
            return IngestReceipt(kind, message or texts.INGEST_ALL_DUPLICATES)
        if kind == InputKind.FILE:
            assert payload.file is not None
            name = payload.file.name if payload.file_name is None else payload.file_name
            ext = Path(name).suffix.lower()
            data = payload.file.data
            material = "chat" if ext == ".json" else "file"
            src_id = await ctx.add(
                material,
                "reference",
                "file",
                _bytes_hash(data),
                meta={**meta, "file_name": name, "owner_tg_id": payload.owner_tg_id},
            )
            if src_id is not None:
                await self._blobs.put(str(src_id), data)
                ctx.tally["files"] += 1
            created = len(ctx.created) > before
            message = texts.ingest_accepted_file(name) if created else texts.INGEST_FILE_DUPLICATE
            return IngestReceipt(kind, message)
        if kind in (InputKind.LINKS, InputKind.ARTICLE):
            dups = ctx.dups
            for ref in extract_post_urls(text):
                mine = ref.handle is not None and ref.handle.lower() == ctx.own_handle
                material = "own_post" if mine else "liked_sample"
                if await ctx.add_material(
                    material,
                    "link_oembed",
                    content_hash(f"x-post:{ref.id}"),
                    external_id=ref.id,
                    url=ref.url,
                    author_handle=ref.handle,
                    meta={**meta, "ask_kind": True},
                ):
                    ctx.tally["links"] += 1
            for url in article_urls(text):
                if await ctx.add(
                    "article",
                    "reference",
                    "url_fetch",
                    content_hash(f"url:{url}"),
                    url=url,
                    owner_note=payload.note,
                    meta=meta,
                ):
                    ctx.tally["articles"] += 1
            n, dups = len(ctx.created) - before, ctx.dups - dups
            if not n:
                message = texts.INGEST_ALL_DUPLICATES
            elif kind == InputKind.ARTICLE:
                message = texts.ingest_accepted_batch({"articles": n, "duplicates": dups})
            else:
                message = texts.ingest_accepted_links(n, dups)
            return IngestReceipt(kind, message)
        if kind == InputKind.HANDLE:
            handle = (parse_handle(text) or "").lower()
            own = handle == ctx.own_handle
            if not own and ctx.read_mode != "paid":
                return IngestReceipt(
                    kind,
                    texts.ingest_foreign_handle(handle),
                    offer_paid=True,
                    keyboard=keyboards.ingest_foreign_handle(),
                )
            await ctx.add_material(
                "own_post" if own else "competitor_post",
                "x_api",
                content_hash(f"x-handle:{handle}"),
                author_handle=handle,
                meta=meta,
            )
            created = len(ctx.created) > before
            message = (
                texts.ingest_accepted_handle(handle)
                if created
                else texts.INGEST_OWN_HANDLE_DUPLICATE
            )
            return IngestReceipt(kind, message)
        dups = ctx.dups
        for part in split_posts(text):
            material = "own_post" if len(part) <= POST_MAX_CHARS else "note"
            src_id = await ctx.add_material(
                material,
                "manual_text",
                content_hash(part),
                owner_note=payload.note,
                meta={**meta, "ask_kind": True},
            )
            if src_id is not None:
                await ctx.sources.add_document(src_id, text=part)
                ctx.tally["texts"] += 1
        n, dups = len(ctx.created) - before, ctx.dups - dups
        message = texts.ingest_accepted_posts(n, dups) if n else texts.INGEST_ALL_DUPLICATES
        return IngestReceipt(kind, message)

    async def _accept_forward(
        self, ctx: _AcceptCtx, payload: IngestPayload, text: str, meta: dict[str, Any]
    ) -> IngestReceipt:
        """Пересланное сообщение Telegram: текст и автор пересылки (ТЗ v3.1)."""
        fwd = payload.forward
        assert fwd is not None
        if not text:
            return IngestReceipt(InputKind.TEXT, texts.INGEST_EMPTY, rejected=True)
        handle = fwd.username.lower() if fwd.username else None
        forward_from = _forward_meta(fwd)
        is_post = len(text) <= POST_MAX_CHARS
        src_id = await ctx.add(
            "post" if is_post else "note",
            "inspiration" if is_post else "reference",
            "tg_forward",
            content_hash(text),
            author_handle=handle,
            owner_note=payload.note,
            meta={**meta, "forward_from": forward_from},
        )
        if src_id is None:
            return IngestReceipt(InputKind.TEXT, texts.INGEST_ALL_DUPLICATES)
        await ctx.sources.add_document(src_id, text=text)
        ctx.tally["texts"] += 1
        return IngestReceipt(InputKind.TEXT, texts.ingest_accepted_posts(1, 0))

    async def set_kind(self, account_id: UUID, batch_id: UUID, kind: str) -> int:
        """Ответ на «Это что?»: вид всех обработанных источников партии."""
        if kind not in ASK_KINDS:
            raise ValueError(f"неизвестный вид: {kind}")
        src_kind, stance = material_kind_stance(kind)
        async with self._uow() as uow:
            return await SourcesRepo(uow.session, account_id).set_kind_for_batch(
                str(batch_id), kind=src_kind, stance=stance
            )

    async def set_stance(self, account_id: UUID, batch_id: UUID, stance: str) -> int:
        """Ответ на вопрос об отношении: stance всех обработанных источников партии."""
        if stance not in STANCES:
            raise ValueError(f"неизвестное отношение: {stance}")
        async with self._uow() as uow:
            return await SourcesRepo(uow.session, account_id).set_stance_for_batch(
                str(batch_id), stance
            )

    async def remove(self, source_id: UUID, user_id: UUID, *, account_id: UUID) -> int:
        """«Убрать материал»: removed_at, отзыв доказательств; вернуть число убранных (0/1)."""
        async with self._uow() as uow:
            return await self._remove_in(uow.session, account_id, source_id, user_id)

    async def remove_batch(self, account_id: UUID, batch_id: UUID, user_id: UUID) -> int:
        """«Убрать» под итогом партии: все её материалы."""
        async with self._uow() as uow:
            ids = await SourcesRepo(uow.session, account_id).batch_ids(str(batch_id))
            removed = 0
            for src_id in ids:
                removed += await self._remove_in(uow.session, account_id, src_id, user_id)
            return removed

    async def _remove_in(
        self, session: DbSession, account_id: UUID, source_id: UUID, user_id: UUID
    ) -> int:
        sources = SourcesRepo(session, account_id)
        src = await sources.get_for_update(source_id)
        if src is None or src.removed_at is not None:
            return 0
        revoked = await sources.active_evidence(source_id)
        await sources.mark_removed(source_id, at=self._clock.now())
        await emit(
            session,
            account_id=account_id,
            type=EventType.MATERIAL_REMOVED,
            actor="user",
            user_id=user_id,
            entity=("source", source_id),
            props={"source_id": str(source_id), "revoked_evidence": revoked},
        )
        return 1

    async def confirm_counter(
        self, account_id: UUID, source_id: UUID, name: str, value: int | None, *, user_id: UUID
    ) -> None:
        """Владелец подтвердил счётчик со скриншота: новое наблюдение с уверенностью 1.

        `value=None` («Не важно») — цифра из наблюдения убирается, остальные подтверждаются.
        """
        if name not in COUNTERS:
            raise ValueError(f"неизвестный счётчик: {name}")
        async with self._uow() as uow:
            session = uow.session
            await SourcesRepo(session, account_id).require(source_id)
            signals = MaterialSignalsRepo(session, account_id)
            last = await signals.latest(source_id, "screenshot")
            if last is None:
                raise ValueError("у материала нет наблюдения со скриншота")
            fields = {c: getattr(last, c) for c in (*COUNTERS, "author_followers", "quotes")}
            fields[name] = value
            signal = await signals.add(
                source_id=source_id,
                origin="screenshot",
                observed_at=self._clock.now(),
                read_confidence=Decimal("1"),
                confirmed_by=user_id,
                raw={"confirmed": name, "previous": str(last.id)},
                **fields,
            )
            await self._signal_event(session, account_id, source_id, signal.id, "screenshot")

    async def screenshot_as_text(self, account_id: UUID, source_id: UUID) -> bool:
        """«Разобрать как текст»: скрин не с поста X становится заметкой; False — нечего делать."""
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            src = await sources.get_for_update(source_id)
            if src is None or src.status != "excluded" or src.meta.get("reason") != "not_a_post":
                return False
            raw = src.meta.get("screenshot")
            text = raw.get("text") if isinstance(raw, dict) else None
            if not isinstance(text, str) or not text.strip():
                return False
            await sources.update(source_id, kind="note", stance="reference", status="new")
            await sources.merge_meta(source_id, reason=None, as_text=True)
            await sources.add_document(source_id, text=text)
            await self._enqueue(JobsRepo(session), account_id, source_id, ":as_text")
        return True

    # --- обработка --------------------------------------------------------------------------

    async def process(self, source_id: UUID, *, account_id: UUID) -> ProcessResult:
        """Довести источник до конечного статуса; повторный вызов ничего не меняет."""
        async with self._uow() as uow:
            src = await SourcesRepo(uow.session, account_id).get(source_id)
            if src is None:
                log.warning("ingest_source_missing", source_id=str(source_id))
                return ProcessResult("missing")
            status = src.status
        if status in TERMINAL:
            return await self._result(account_id, source_id)
        if status == "new" and not await self._parse(account_id, source_id):
            return await self._result(account_id, source_id)
        return await self._index(account_id, source_id)

    async def _result(self, account_id: UUID, source_id: UUID) -> ProcessResult:
        async with self._uow() as uow:
            src = await SourcesRepo(uow.session, account_id).require(source_id)
            chunks = await ChunksRepo(uow.session, account_id).count_for_source(source_id)
            return ProcessResult(src.status, chunks=chunks)

    async def _parse(self, account_id: UUID, source_id: UUID) -> bool:
        """new → parsed: извлечь текст (X, файл, скрин, статья), почистить, записать документы."""
        async with self._uow() as uow:
            src = await SourcesRepo(uow.session, account_id).require(source_id)
            account = await AccountsRepo(uow.session).get(account_id)
            assert account is not None
            kind, stance, url, external_id = src.kind, src.stance, src.url, src.external_id
            handle, meta, origin, note = (
                src.author_handle,
                dict(src.meta),
                src.origin,
                src.owner_note,
            )
            existing = await SourcesRepo(uow.session, account_id).documents(source_id)
            own_handle, mode = account.handle.lower(), account.x_read_mode
        patch: dict[str, Any] = {}
        updates: dict[str, Any] = {}
        extra = _Extra()
        try:
            if existing:
                docs: list[ParsedDoc] = []
            elif origin == "screenshot":
                docs = await self._read_screenshot(
                    account_id, source_id, meta, note, extra, updates
                )
            elif kind == "article" and url:
                docs = await self._fetch_article_doc(url, patch)
            elif url or external_id:
                docs = await self._fetch_post(account_id, url, external_id, handle, updates, extra)
            elif kind in PII_KINDS:
                docs = await self._parse_file(source_id, meta, patch)
            elif handle is not None:
                docs = await self._fetch_handle(account_id, stance, handle, own_handle, mode)
            else:
                raise _Stop("failed", "empty", texts.INGEST_EMPTY_TEXT)
        except _Stop as stop:
            await self._close(account_id, source_id, stop)
            return False
        if extra.repost is not None:
            await self._close_repost(account_id, source_id, extra.repost, updates, meta)
            return False
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            locked = await sources.get_for_update(source_id)
            if locked is None or locked.status != "new":
                return locked is not None and locked.status == "parsed"  # другой обработчик успел
            stored = 0
            for doc in await sources.documents(source_id):
                raw, clean = self._prepare(kind, doc.text)
                await sources.update_document(
                    doc.id, text=raw, text_clean=clean, token_count=count_tokens(clean)
                )
                stored += bool(clean)
            for parsed in docs:
                raw, clean = self._prepare(kind, parsed.text)
                if not clean:
                    continue
                await sources.add_document(
                    source_id, text=raw, text_clean=clean, token_count=count_tokens(clean)
                )
                stored += 1
            if stored:
                if extra.screenshot is not None:
                    patch["read_line"] = await self._apply_screenshot(
                        session, account_id, source_id, extra.screenshot, own_handle, updates
                    )
                await sources.update(source_id, status="parsed", **updates)
                if patch:
                    await sources.merge_meta(source_id, **patch)
        if not stored:
            await self._close(
                account_id, source_id, _Stop("failed", "empty", texts.INGEST_EMPTY_TEXT)
            )
        elif extra.screenshot is not None:
            await self._ask_doubtful(account_id, source_id, extra.screenshot, patch["read_line"])
        return bool(stored)

    # --- ТЗ v3.1: скриншот, статья, репост ---------------------------------------------------

    async def _read_screenshot(
        self,
        account_id: UUID,
        source_id: UUID,
        meta: dict[str, Any],
        note: str | None,
        extra: _Extra,
        updates: dict[str, Any],
    ) -> list[ParsedDoc]:
        """Скрин → ScreenshotRead (сохраняется в meta: повтор задачи не читает заново)."""
        raw = meta.get("screenshot")
        if isinstance(raw, dict):
            read = ScreenshotRead.model_validate(raw)
        else:
            data = await self._blobs.get(str(source_id))
            if data is None:
                raise _Stop("failed", "lost", texts.SCREENSHOT_LOST)
            mime = str(meta.get("image_mime") or "image/png")
            try:
                read = await self._screenshots.read_screenshot(
                    data, note, account_id=account_id, mime=mime
                )
            except ScreenshotUnavailable as exc:
                raise _Stop("failed", "screenshot_off", exc.user_message) from None
            except (LLMOutputError, PermanentProviderError):
                raise _Stop("failed", "unreadable", texts.SCREENSHOT_UNREADABLE) from None
            async with self._uow() as uow:
                await SourcesRepo(uow.session, account_id).merge_meta(
                    source_id, screenshot=read.model_dump(mode="json")
                )
            await self._blobs.delete(str(source_id))
        if not read.is_x_post or read.kind in ("profile", "other"):
            raise _Stop(
                "excluded",
                "not_a_post",
                texts.SCREENSHOT_NOT_POST,
                keyboards.screenshot_not_post(str(source_id)),
            )
        if not read.text.strip():
            raise _Stop("failed", "empty", texts.INGEST_EMPTY_TEXT)
        extra.screenshot = read
        updates["kind"] = SCREENSHOT_KINDS[read.kind]
        if read.author_handle:
            updates["author_handle"] = read.author_handle.strip().lstrip("@").lower()
        return [ParsedDoc(read.text)]

    async def _apply_screenshot(
        self,
        session: DbSession,
        account_id: UUID,
        source_id: UUID,
        read: ScreenshotRead,
        own_handle: str,
        updates: dict[str, Any],
    ) -> str:
        """Автор (+подписчики), первый сигнал, исходный пост ответа/цитаты; вернуть строку
        «Прочитал скрин: …»."""
        now = self._clock.now()
        authors = AuthorsRepo(session, account_id)
        handle = updates.get("author_handle")
        followers = read.author_followers.value if read.author_followers else None
        if isinstance(handle, str):
            author_id = await _author_id(authors, handle, own_handle)
            if author_id is not None:
                updates["author_id"] = author_id
                if followers is not None:
                    await authors.update(
                        author_id,
                        followers=followers,
                        followers_observed_at=now,
                        followers_origin="screenshot",
                    )
        counters = {name: c for name in COUNTERS if (c := getattr(read, name)) is not None}
        shown = [(n, c.value) for n, c in counters.items() if c.value is not None]
        if counters or followers is not None:
            confidences = [c.confidence for c in counters.values()]
            signal = await MaterialSignalsRepo(session, account_id).add(
                source_id=source_id,
                origin="screenshot",
                observed_at=now,
                author_followers=followers,
                read_confidence=Decimal(str(round(min(confidences, default=1.0), 2))),
                raw=read.model_dump(mode="json"),
                **{n: c.value for n, c in counters.items()},
            )
            await self._signal_event(session, account_id, source_id, signal.id, "screenshot")
        if read.kind in ("reply", "quote") and read.parent_text and read.parent_text.strip():
            parent_id = await self._context_source(
                session,
                account_id,
                read.parent_text,
                handle=read.parent_author_handle,
                own_handle=own_handle,
                stance="reference",
                origin="screenshot",
            )
            await SourcesRepo(session, account_id).set_parent(source_id, parent_id)
        line_handle = handle if isinstance(handle, str) else None
        main = [(n, v) for n, v in shown if n in ("views", "likes", "replies")]
        return texts.screenshot_read_line(line_handle, main)

    async def _signal_event(
        self, session: DbSession, account_id: UUID, source_id: UUID, signal_id: UUID, origin: str
    ) -> None:
        await emit(
            session,
            account_id=account_id,
            type=EventType.MATERIAL_SIGNAL_ADDED,
            entity=("source", source_id),
            props={"source_id": str(source_id), "signal_id": str(signal_id), "origin": origin},
        )

    async def _context_source(
        self,
        session: DbSession,
        account_id: UUID,
        text: str,
        *,
        handle: str | None,
        own_handle: str,
        stance: str,
        origin: str,
        meta: dict[str, Any] | None = None,
    ) -> UUID:
        """Исходный пост (для ответа, цитаты, репоста): источник с текстом и задачей разбора."""
        authors = AuthorsRepo(session, account_id)
        sources = SourcesRepo(session, account_id)
        norm = handle.strip().lstrip("@").lower() if handle else None
        author_id = await _author_id(authors, norm, own_handle) if norm else None
        src, new = await sources.add_or_get(
            kind="post",
            stance=stance,
            origin=origin,
            content_hash=content_hash(text),
            author_handle=norm,
            author_id=author_id,
            meta=meta or {},
        )
        if new:
            await sources.add_document(src.id, text=text)
            await self._enqueue(JobsRepo(session), account_id, src.id)
        return src.id

    async def _ask_doubtful(
        self, account_id: UUID, source_id: UUID, read: ScreenshotRead, read_line: str
    ) -> None:
        """Вопрос владельцу о первой сомнительной цифре скрина."""
        for name, counter in doubtful(read):
            options = alternatives(counter)
            if len(options) < 2:
                continue
            await self._notifier.notify(
                account_id,
                texts.screenshot_doubt(read_line, name, [shown for shown, _ in options[:2]]),
                keyboard=keyboards.screenshot_doubt(str(source_id), name, options[:2]),
            )
            return

    async def _fetch_article_doc(self, url: str, patch: dict[str, Any]) -> list[ParsedDoc]:
        try:
            doc = await self._fetch_article(url)
        except ParseError as exc:
            message = texts.ingest_article_failed(exc.user_message)
            raise _Stop("failed", "article", message) from None
        patch.update(title=doc.meta.get("title"), words=doc.meta.get("words"))
        return [doc]

    async def _close_repost(
        self,
        account_id: UUID,
        source_id: UUID,
        repost: tuple[str, str],
        updates: dict[str, Any],
        meta: dict[str, Any],
    ) -> None:
        """Репост: материал — исходный пост (с сигналом owner_pick), сам репост — связь."""
        handle, text = repost
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            locked = await sources.get_for_update(source_id)
            if locked is None or locked.status != "new":
                return
            account = await AccountsRepo(session).get(account_id)
            assert account is not None
            batch: dict[str, Any] = {k: meta[k] for k in ("batch", "accepted_by") if k in meta}
            original = await self._context_source(
                session,
                account_id,
                text,
                handle=handle,
                own_handle=account.handle.lower(),
                stance="inspiration",
                origin=str(updates.get("origin") or "x_api"),
                meta=batch,
            )
            await sources.set_parent(source_id, original)
            signal = await MaterialSignalsRepo(session, account_id).add(
                source_id=original, origin="owner_pick", observed_at=self._clock.now()
            )
            await self._signal_event(session, account_id, original, signal.id, "owner_pick")
            await sources.update(
                source_id, status="excluded", kind="repost", **_post_fields(updates)
            )
            await sources.merge_meta(source_id, reason="repost", repost_of=str(original))
        await self._maybe_summarize(account_id, source_id)

    @staticmethod
    def _prepare(kind: str, text: str) -> tuple[str, str]:
        """(текст для documents.text, очищенный текст); в переписках и файлах — без PII."""
        if kind in PII_KINDS:
            text = scrub_pii(text).text
        return text, normalize(text)

    async def _fetch_post(
        self,
        account_id: UUID,
        url: str | None,
        external_id: str | None,
        handle: str | None,
        updates: dict[str, Any],
        extra: _Extra,
    ) -> list[ParsedDoc]:
        ref = parse_post_url(url) if url else None
        if ref is None:
            if external_id is None:
                raise _Stop("failed", "bad_url", texts.ingest_post_not_found(url or "—"))
            ref = PostRef(external_id, handle)
        x = await self._x_for(account_id)
        try:
            post = await x.get_post(ref)
        except XNotFound:
            raise _Stop("failed", "not_found", texts.ingest_post_not_found(ref.url)) from None
        except XModeError:
            raise _Stop("excluded", "x_mode", texts.ingest_x_off()) from None
        updates.update(
            external_id=post.id,
            url=ref.url,
            author_handle=post.author_handle or ref.handle,
            origin="x_api" if post.source == "api" else "link_oembed",
        )
        repost = _REPOST.match(post.text)
        if repost is not None:
            extra.repost = (repost.group(1), repost.group(2).strip())
            return []
        return xpost.parse(post)

    async def _fetch_handle(
        self, account_id: UUID, stance: str, handle: str, own_handle: str, mode: str
    ) -> list[ParsedDoc]:
        if stance != OWN_STANCE and handle.lower() != own_handle:
            if mode != "paid":
                raise _Stop(
                    "excluded",
                    "x_mode",
                    texts.ingest_foreign_handle(handle),
                    keyboards.ingest_foreign_handle(),
                )
            # ленты чужого аккаунта у XClient нет — посты конкурентов соберёт радар
            raise _Stop("excluded", "radar", None)
        x = await self._x_for(account_id)
        try:
            posts = await x.get_own_posts(limit=OWN_POSTS_LIMIT)
        except XModeError:
            raise _Stop("excluded", "x_mode", texts.ingest_x_off()) from None
        docs = [d for p in posts for d in xpost.parse(p)]
        if not docs:
            raise _Stop("excluded", "empty", None)
        return docs

    async def _parse_file(
        self, source_id: UUID, meta: dict[str, Any], patch: dict[str, Any]
    ) -> list[ParsedDoc]:
        name = str(meta.get("file_name") or "файл")
        data = await self._blobs.get(str(source_id))
        if data is None:
            raise _Stop("failed", "lost", texts.INGEST_FILE_LOST)
        owner = meta.get("owner_tg_id")
        try:
            docs = await asyncio.to_thread(
                parse_file, name, data, owner_tg_id=owner if isinstance(owner, int) else None
            )
        except NoTextLayer as exc:
            raise _Stop("failed", "no_text", exc.user_message) from None
        except ParseError as exc:
            message = texts.ingest_parse_failed(name, exc.user_message)
            raise _Stop("failed", "parse", message) from None
        finally:
            await self._blobs.delete(str(source_id))
        for doc in docs:
            for key in ("pages", "messages", "paragraphs"):
                if key in doc.meta:
                    patch[key] = doc.meta[key]
        return docs

    async def _close(self, account_id: UUID, source_id: UUID, stop: _Stop) -> None:
        """Закрыть источник (failed / excluded), записать событие и сообщить владельцу."""
        async with self._uow() as uow:
            await self._fail_in(uow.session, account_id, source_id, stop)
        if stop.message:
            await self._notifier.notify(account_id, stop.message, keyboard=stop.keyboard)
        await self._maybe_summarize(account_id, source_id)

    async def _fail_in(
        self, session: DbSession, account_id: UUID, source_id: UUID, stop: _Stop
    ) -> None:
        sources = SourcesRepo(session, account_id)
        await sources.update(source_id, status=stop.status)
        await sources.merge_meta(source_id, reason=stop.reason)
        if stop.status == "failed":
            await emit(
                session,
                account_id=account_id,
                type=EventType.SOURCE_FAILED,
                entity=("source", source_id),
                props={"source_id": str(source_id), "reason": stop.reason},
            )

    async def _screen(self, account_id: UUID, source_id: UUID) -> str | None:
        """Проверка на входе (ТЗ v3.1): причина карантина или None (допуск).

        Автор заблокирован — карантин без вызова модели. Свои материалы (stance own) не
        проверяются. Итог допуска пишется в meta.screen: повтор задачи не зовёт судью снова.
        """
        async with self._uow() as uow:
            session = uow.session
            src = await SourcesRepo(session, account_id).require(source_id)
            if src.author_id is not None:
                author = await AuthorsRepo(session, account_id).get(src.author_id)
                if author is not None and author.trust < 0:
                    return REASON_AUTHOR_BLOCKED
            if self._screener is None or src.stance == OWN_STANCE or src.meta.get("screen"):
                return None
            docs = await SourcesRepo(session, account_id).documents(source_id)
            text = "\n\n".join(d.text_clean or "" for d in docs).strip()
        if not text:
            return None
        verdict = await self._screener.screen(
            text, account_id=account_id, entity=("source", source_id)
        )
        if not verdict.admit:
            return verdict.reason or "screen"
        async with self._uow() as uow:
            await SourcesRepo(uow.session, account_id).merge_meta(
                source_id, screen="admitted" if verdict.by != "unavailable" else "unavailable"
            )
        return None

    async def _quarantine(self, account_id: UUID, source_id: UUID, reason: str) -> ProcessResult:
        """Карантин: не индексируется и не разбирается; событие material.quarantined."""
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            locked = await sources.get_for_update(source_id)
            if locked is None or locked.status != "parsed":
                return await self._result_in(session, account_id, source_id)
            await sources.quarantine(source_id, reason)
            await sources.update(source_id, status="excluded")
            await sources.merge_meta(source_id, reason="quarantine", quarantine=reason)
            await emit(
                session,
                account_id=account_id,
                type=EventType.MATERIAL_QUARANTINED,
                entity=("source", source_id),
                props={"source_id": str(source_id), "reason": reason},
            )
        log.info("ingest_quarantined", source_id=str(source_id), reason=reason)
        await self._maybe_summarize(account_id, source_id)
        return ProcessResult("excluded")

    async def _index(self, account_id: UUID, source_id: UUID) -> ProcessResult:
        """parsed → indexed: куски, близкий дедуп, теги, векторы — одной транзакцией в конце."""
        reason = await self._screen(account_id, source_id)
        if reason is not None:
            return await self._quarantine(account_id, source_id, reason)
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            src = await sources.require(source_id)
            if src.status != "parsed":
                return await self._result_in(session, account_id, source_id)
            model = await EmbeddingModelsRepo(session).active()
            if model is None or model.dim != self._embedder.dim:
                raise ConfigError(
                    f"эмбеддер {self._embedder.model_name}/{self._embedder.dim} не совпадает "
                    f"с реестром ({model.name if model else '—'}/{model.dim if model else '—'})"
                )
            if model.name != self._embedder.model_name:
                raise ConfigError(f"активная модель в реестре — {model.name}")
            model_id = model.id
            kind, stance, meta = src.kind, src.stance, dict(src.meta)
            account = await AccountsRepo(session).get(account_id)
            own_handle = account.handle if account else None
            pieces: list[_Piece] = []
            dup_chunks: list[UUID] = []
            for doc in await sources.documents(source_id):
                clean = doc.text_clean or ""
                parts = [clean] if kind in POST_KINDS else chunk(clean)
                for idx, text in enumerate(p for p in parts if p.strip()):
                    near = await find_near_duplicate(
                        session, account_id, text, exclude_source_id=source_id
                    )
                    if near is not None:
                        dup_chunks.append(near)
                    else:
                        pieces.append(_Piece(doc.id, idx, text))
            dup_of = (
                await ChunksRepo(session, account_id).source_of(dup_chunks[0])
                if dup_chunks
                else None
            )

        if not pieces:
            stop = _Stop("excluded", "duplicate", None)
            async with self._uow() as uow:
                sources = SourcesRepo(uow.session, account_id)
                locked = await sources.get_for_update(source_id)
                if locked is not None and locked.status == "parsed":
                    await self._fail_in(uow.session, account_id, source_id, stop)
                    await sources.merge_meta(
                        source_id,
                        duplicate_of=str(dup_of) if dup_of else None,
                        near_duplicate_of=str(dup_chunks[0]) if dup_chunks else None,
                    )
                    await emit(
                        uow.session,
                        account_id=account_id,
                        type=EventType.SOURCE_INGESTED,
                        entity=("source", source_id),
                        props={
                            "source_id": str(source_id),
                            "kind": source_material(kind, stance),
                            "chunks": 0,
                            "duplicates": len(dup_chunks),
                        },
                    )
            await self._maybe_summarize(account_id, source_id)
            return ProcessResult("excluded", duplicates=len(dup_chunks))

        texts_ = [p.text for p in pieces]
        tags = await self._tagger.tag_batch(account_id, texts_, own_handle=own_handle)
        vectors: list[list[float]] = []
        for part in batched(texts_):
            vectors += await asyncio.to_thread(self._embedder.embed, part)
        if len(vectors) != len(pieces) or any(len(v) != self._embedder.dim for v in vectors):
            raise ConfigError("эмбеддер вернул не то число или не ту размерность векторов")

        guess = _guess_kind(tags) if meta.get("ask_kind") else None
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            locked = await sources.get_for_update(source_id)
            if locked is None or locked.status != "parsed":
                return await self._result_in(session, account_id, source_id)
            chunks_repo = ChunksRepo(session, account_id)
            for piece, tag, vec in zip(pieces, tags, vectors, strict=True):
                row = await chunks_repo.add_chunk(
                    piece.document_id,
                    idx=piece.idx,
                    text=piece.text,
                    tags=tag.as_db(),
                    lang=tag.lang,
                )
                await chunks_repo.add_embedding(row.id, model_id=model_id, vec=vec)
            final = guess or source_material(kind, stance)
            if guess:
                guess_kind, guess_stance = material_kind_stance(guess)
                await sources.update(
                    source_id, status="indexed", kind=guess_kind, stance=guess_stance
                )
            else:
                await sources.update(source_id, status="indexed")
            if dup_chunks:
                await sources.merge_meta(source_id, near_duplicates=[str(c) for c in dup_chunks])
            await emit(
                session,
                account_id=account_id,
                type=EventType.SOURCE_INGESTED,
                entity=("source", source_id),
                props={
                    "source_id": str(source_id),
                    "kind": final,
                    "chunks": len(pieces),
                    "duplicates": len(dup_chunks),
                },
            )
        await self._maybe_summarize(account_id, source_id)
        return ProcessResult("indexed", chunks=len(pieces), duplicates=len(dup_chunks))

    async def _result_in(
        self, session: DbSession, account_id: UUID, source_id: UUID
    ) -> ProcessResult:
        src = await SourcesRepo(session, account_id).require(source_id)
        chunks = await ChunksRepo(session, account_id).count_for_source(source_id)
        return ProcessResult(src.status, chunks=chunks)

    # --- итог партии ------------------------------------------------------------------------

    async def _maybe_summarize(self, account_id: UUID, source_id: UUID) -> None:
        """Когда все источники партии закончены — один итог владельцу (с вопросом «Это что?»)."""
        async with self._uow() as uow:
            session = uow.session
            sources = SourcesRepo(session, account_id)
            src = await sources.get(source_id)
            batch = src.meta.get("batch") if src is not None else None
            if not isinstance(batch, str):
                return
            rows = await sources.lock_batch(batch)
            if not rows or any(r.status not in TERMINAL for r in rows):
                return
            if any(r.meta.get("summary_sent") for r in rows):
                return
            await sources.merge_meta(rows[0].id, summary_sent=True)
            indexed = [r for r in rows if r.status == "indexed"]
            tag_rows = await ChunksRepo(session, account_id).tags_for_sources(
                [r.id for r in indexed]
            )
            topics = _top_topics(tag_rows)
            near = sum(1 for r in rows if r.meta.get("reason") == "duplicate")
            dups = int(rows[0].meta.get("batch_dups", 0)) + near
            quarantined = dict(
                Counter(
                    str(r.meta.get("quarantine"))
                    for r in rows
                    if r.meta.get("reason") == "quarantine"
                )
            )
            keyboard = None
            if rows[0].kind in PII_KINDS and not rows[0].meta.get("multi"):
                if not indexed:
                    return
                chunks = await ChunksRepo(session, account_id).count_for_source(indexed[0].id)
                pages = indexed[0].meta.get("pages")
                message = texts.ingest_file_summary(
                    pages if isinstance(pages, int) else None, chunks, topics, dups
                )
            elif rows[0].meta.get("multi"):
                message = texts.ingest_batch_summary(len(indexed), dups, quarantined)
                if topics:
                    message += f" Темы: {', '.join(topics)}."
                if indexed:
                    message += f"\n\n{texts.INGEST_KIND_QUESTION}"
            else:
                by_kind = Counter(source_material(r.kind, r.stance) for r in indexed)
                total = await sources.count_documents(status="indexed", stance=OWN_STANCE)
                message = texts.ingest_summary(dict(by_kind), total, topics, dups, bool(indexed))
                message += texts.ingest_quarantine_note(quarantined)
                if len(indexed) == 1:
                    message = _summary_prefix(indexed[0].meta, indexed[0].kind) + message
            if indexed and rows[0].kind not in PII_KINDS:
                default = Counter(r.stance for r in indexed).most_common(1)[0][0]
                keyboard = keyboards.ingest_relation(batch, default)
        if not indexed and not dups and not quarantined:
            return  # всё упало — владелец уже получил сообщения об ошибках
        await self._notifier.notify(account_id, message, keyboard=keyboard)


def _summary_prefix(meta: dict[str, Any], kind: str) -> str:
    """Строка перед итогом одиночного материала: что прочитано со скрина или из статьи."""
    line = meta.get("read_line")
    if isinstance(line, str) and line:
        return line + "\n\n"
    if kind == "article":
        words = meta.get("words")
        title = meta.get("title")
        return (
            texts.ingest_article_summary(
                title if isinstance(title, str) else None, words if isinstance(words, int) else 0
            )
            + "\n\n"
        )
    return ""


def _kind_stance(material: str) -> tuple[str, str]:
    """Материал бота → (kind, stance); файл и переписка — справочные материалы."""
    if material in PII_KINDS:
        return material, "reference"
    return material_kind_stance(material)


async def _author_id(authors: AuthorsRepo, handle: str, own_handle: str) -> UUID | None:
    """Автор материала (ТЗ v3.1); свой хэндл — автор «я сам»; не хэндл X — без автора."""
    try:
        if handle.lower() == own_handle:
            return (await authors.set_self(handle)).id
        return (await authors.get_or_create(handle)).id
    except ValueError:
        return None


def _bytes_hash(data: bytes, prefix: str = "file") -> str:
    return f"{prefix}:" + hashlib.sha256(data).hexdigest()


def _guess_kind(tags: Sequence[Tags]) -> str | None:
    votes = Counter(t.material for t in tags if t.material)
    return votes.most_common(1)[0][0] if votes else None


def _top_topics(tag_rows: Sequence[Sequence[str]], limit: int = 3) -> list[str]:
    counter: Counter[str] = Counter()
    for row in tag_rows:
        for tag in row:
            if tag.startswith("topic:"):
                counter[tag.removeprefix("topic:")] += 1
    return [t for t, _ in counter.most_common(limit)]
