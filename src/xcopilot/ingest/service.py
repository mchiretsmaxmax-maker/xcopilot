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
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import structlog

from xcopilot.bot import keyboards, texts
from xcopilot.clock import Clock
from xcopilot.db.repos import (
    AccountsRepo,
    AuthorsRepo,
    ChunksRepo,
    EmbeddingModelsRepo,
    SourcesRepo,
)
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.repos.sources import OWN_STANCE, material_kind_stance, source_material
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.embeddings.embedder import Embedder, batched
from xcopilot.errors import ConfigError
from xcopilot.ingest.blobs import BlobStore
from xcopilot.ingest.chunker import chunk, count_tokens
from xcopilot.ingest.classify import Incoming, InputKind, classify
from xcopilot.ingest.clean import normalize
from xcopilot.ingest.dedupe import content_hash, find_near_duplicate
from xcopilot.ingest.errors import NoTextLayer, ParseError
from xcopilot.ingest.parsers import SUPPORTED_EXTENSIONS, parse_file, xpost
from xcopilot.ingest.parsers.base import MAX_FILE_BYTES, MAX_TEXT_CHARS, ParsedDoc
from xcopilot.ingest.scrub import scrub_pii
from xcopilot.ingest.tagger import Tagger, Tags
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


@dataclass(frozen=True, slots=True)
class FilePayload:
    name: str
    data: bytes


@dataclass(frozen=True, slots=True)
class IngestPayload:
    """Вход от пользователя: текст или файл; owner_tg_id — для экспорта Telegram."""

    text: str | None = None
    file: FilePayload | None = None
    file_name: str | None = None
    file_size: int | None = None
    owner_tg_id: int | None = None


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
class _Piece:
    document_id: UUID
    idx: int
    text: str


class _Stop(Exception):
    """Источник закрыт без индексации (failed / excluded); message — владельцу (или None)."""

    def __init__(self, status: str, reason: str, message: str | None, keyboard: Any = None) -> None:
        super().__init__(reason)
        self.status, self.reason, self.message, self.keyboard = status, reason, message, keyboard


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
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._tagger = tagger
        self._embedder = embedder
        self._x_for = x_for
        self._notifier = notifier
        self._blobs = blobs
        self._max_attempts = max_attempts

    # --- приём ------------------------------------------------------------------------------

    async def accept(
        self, account_id: UUID, user_id: UUID, payload: IngestPayload
    ) -> IngestReceipt:
        """Принять вход: создать источники (без дублей) и поставить задачи ingest.process."""
        name = payload.file_name or (payload.file.name if payload.file else None)
        size = payload.file_size
        if size is None and payload.file is not None:
            size = len(payload.file.data)
        kind = classify(Incoming(text=payload.text, file_name=name, file_size=size))
        if kind == InputKind.EMPTY:
            return IngestReceipt(kind, texts.INGEST_EMPTY, rejected=True)
        if kind == InputKind.FILE:
            assert name is not None
            refusal = file_precheck(name, size)
            if refusal is not None:
                return IngestReceipt(kind, refusal, rejected=True)
            if payload.file is None:
                raise ValueError("accept: файл нужно скачать до приёма")
        text = (payload.text or "").strip()
        if kind != InputKind.FILE and len(text) > MAX_TEXT_CHARS:
            return IngestReceipt(kind, texts.INGEST_TEXT_TOO_LONG, rejected=True)

        batch = uuid.uuid4()
        async with self._uow() as uow:
            session = uow.session
            account = await AccountsRepo(session).get(account_id)
            if account is None:
                raise ConfigError(f"аккаунт {account_id} не найден")
            sources = SourcesRepo(session, account_id)
            authors = AuthorsRepo(session, account_id)
            own_handle_l = account.handle.lower()
            created: list[UUID] = []
            dups = 0
            meta: dict[str, Any] = {"batch": str(batch), "accepted_by": str(user_id)}

            async def add(material: str, origin: str, chash: str, **fields: Any) -> UUID | None:
                nonlocal dups
                src_kind, stance = _kind_stance(material)
                handle = fields.get("author_handle")
                if handle and "author_id" not in fields:
                    fields["author_id"] = await _author_id(authors, handle, own_handle_l)
                src, new = await sources.add_or_get(
                    kind=src_kind, stance=stance, origin=origin, content_hash=chash, **fields
                )
                if not new:
                    dups += 1
                    return None
                created.append(src.id)
                return src.id

            receipt: IngestReceipt
            if kind == InputKind.FILE:
                assert payload.file is not None and name is not None
                ext = Path(name).suffix.lower()
                data = payload.file.data
                src_id = await add(
                    "chat" if ext == ".json" else "file",
                    "file",
                    _bytes_hash(data),
                    meta={**meta, "file_name": name, "owner_tg_id": payload.owner_tg_id},
                )
                if src_id is not None:
                    await self._blobs.put(str(src_id), data)
                message = (
                    texts.ingest_accepted_file(name) if created else texts.INGEST_FILE_DUPLICATE
                )
                receipt = IngestReceipt(kind, message)
            elif kind == InputKind.LINKS:
                own_handle = account.handle.lower()
                for ref in extract_post_urls(text):
                    mine = ref.handle is not None and ref.handle.lower() == own_handle
                    await add(
                        "own_post" if mine else "liked_sample",
                        "link_oembed",
                        content_hash(f"x-post:{ref.id}"),
                        external_id=ref.id,
                        url=ref.url,
                        author_handle=ref.handle,
                        meta={**meta, "ask_kind": True},
                    )
                message = (
                    texts.ingest_accepted_links(len(created), dups)
                    if created
                    else texts.INGEST_ALL_DUPLICATES
                )
                receipt = IngestReceipt(kind, message)
            elif kind == InputKind.HANDLE:
                handle = (parse_handle(text) or "").lower()
                own = handle == account.handle.lower()
                if not own and account.x_read_mode != "paid":
                    receipt = IngestReceipt(
                        kind,
                        texts.ingest_foreign_handle(handle),
                        offer_paid=True,
                        keyboard=keyboards.ingest_foreign_handle(),
                    )
                else:
                    await add(
                        "own_post" if own else "competitor_post",
                        "x_api",
                        content_hash(f"x-handle:{handle}"),
                        author_handle=handle,
                        meta=meta,
                    )
                    if created:
                        message = texts.ingest_accepted_handle(handle)
                    else:
                        message = texts.INGEST_OWN_HANDLE_DUPLICATE
                    receipt = IngestReceipt(kind, message)
            else:
                parts = split_posts(text)
                for part in parts:
                    src_kind = "own_post" if len(part) <= POST_MAX_CHARS else "note"
                    src_id = await add(
                        src_kind,
                        "manual_text",
                        content_hash(part),
                        meta={**meta, "ask_kind": True},
                    )
                    if src_id is not None:
                        await sources.add_document(src_id, text=part)
                message = (
                    texts.ingest_accepted_posts(len(created), dups)
                    if created
                    else texts.INGEST_ALL_DUPLICATES
                )
                receipt = IngestReceipt(kind, message)

            jobs = JobsRepo(session)
            for src_id in created:
                await sources.merge_meta(src_id, batch_dups=dups)
                await jobs.enqueue(
                    account_id=account_id,
                    kind=INGEST_KIND,
                    payload={"source_id": str(src_id)},
                    run_at=self._clock.now(),
                    max_attempts=self._max_attempts,
                    idempotency_key=f"ingest:{src_id}",
                )
            await emit(
                session,
                account_id=account_id,
                type=EventType.INGEST_ACCEPTED,
                actor="user",
                user_id=user_id,
                props={"input": kind.value, "sources": len(created), "duplicates": dups},
            )
        receipt.batch_id = batch if created else None
        receipt.source_ids = created
        receipt.duplicates = dups
        return receipt

    async def set_kind(self, account_id: UUID, batch_id: UUID, kind: str) -> int:
        """Ответ на «Это что?»: вид всех обработанных источников партии."""
        if kind not in ASK_KINDS:
            raise ValueError(f"неизвестный вид: {kind}")
        src_kind, stance = material_kind_stance(kind)
        async with self._uow() as uow:
            return await SourcesRepo(uow.session, account_id).set_kind_for_batch(
                str(batch_id), kind=src_kind, stance=stance
            )

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
        """new → parsed: извлечь текст (X, файл), почистить, вычистить PII, записать документы."""
        async with self._uow() as uow:
            src = await SourcesRepo(uow.session, account_id).require(source_id)
            account = await AccountsRepo(uow.session).get(account_id)
            assert account is not None
            kind, stance, url, external_id = src.kind, src.stance, src.url, src.external_id
            handle, meta = src.author_handle, dict(src.meta)
            existing = await SourcesRepo(uow.session, account_id).documents(source_id)
            own_handle, mode = account.handle.lower(), account.x_read_mode
        patch: dict[str, Any] = {}
        updates: dict[str, Any] = {}
        try:
            if existing:
                docs: list[ParsedDoc] = []
            elif url or external_id:
                docs = await self._fetch_post(account_id, url, external_id, handle, updates)
            elif kind in PII_KINDS:
                docs = await self._parse_file(source_id, meta, patch)
            elif handle is not None:
                docs = await self._fetch_handle(account_id, stance, handle, own_handle, mode)
            else:
                raise _Stop("failed", "empty", texts.INGEST_EMPTY_TEXT)
        except _Stop as stop:
            await self._close(account_id, source_id, stop)
            return False
        async with self._uow() as uow:
            sources = SourcesRepo(uow.session, account_id)
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
                await sources.update(source_id, status="parsed", **updates)
                if patch:
                    await sources.merge_meta(source_id, **patch)
        if not stored:
            await self._close(
                account_id, source_id, _Stop("failed", "empty", texts.INGEST_EMPTY_TEXT)
            )
        return bool(stored)

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

    async def _index(self, account_id: UUID, source_id: UUID) -> ProcessResult:
        """parsed → indexed: куски, близкий дедуп, теги, векторы — одной транзакцией в конце."""
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
            if rows[0].kind in PII_KINDS:
                if not indexed:
                    return
                chunks = await ChunksRepo(session, account_id).count_for_source(indexed[0].id)
                pages = indexed[0].meta.get("pages")
                message = texts.ingest_file_summary(
                    pages if isinstance(pages, int) else None, chunks, topics, dups
                )
                keyboard = None
            else:
                by_kind = Counter(source_material(r.kind, r.stance) for r in indexed)
                total = await sources.count_documents(status="indexed", stance=OWN_STANCE)
                ask = any(r.meta.get("ask_kind") for r in indexed)
                message = texts.ingest_summary(dict(by_kind), total, topics, dups, ask)
                keyboard = None
                if ask and indexed:
                    default = by_kind.most_common(1)[0][0]
                    keyboard = keyboards.ingest_kind(batch, default)
        if not indexed and not dups:
            return  # всё упало — владелец уже получил сообщения об ошибках
        await self._notifier.notify(account_id, message, keyboard=keyboard)


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


def _bytes_hash(data: bytes) -> str:
    return "file:" + hashlib.sha256(data).hexdigest()


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
