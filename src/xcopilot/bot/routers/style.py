"""«🎨 Стиль»: паспорт одной сводкой, файл .md туда и обратно, версии, жёсткие правила.

Экраны (feat/passport-file):
- главный — сводка активной версии и кнопки «📄 Скачать», «📤 Загрузить свой»,
  «🔁 Пересобрать», «🕘 Паспорта», «⚙️ Жёсткие правила»;
- «📤 Загрузить свой» — файл или текст → diff с активным → «✅ Применить» / «✖️ Отменить»;
- «🕘 Паспорта» — версии по 8, карточка версии: показать, сравнить, сделать активной,
  скачать, назвать;
- «⚙️ Жёсткие правила» — переключатели «Регистр: любой ▸ строчные».
Старые callback-и (`st:rules`, `st:hist`, `st:ex`, `st:rb`, `st:keep`) ведут на новые экраны.
"""

from __future__ import annotations

import io
import re
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.one_action import CANCEL_CB
from xcopilot.bot.states import StyleFlow
from xcopilot.errors import NotFound
from xcopilot.style.diff import diff_text
from xcopilot.style.errors import NoOwnPosts, NothingToRollback, UnknownRule
from xcopilot.style.parse_md import PassportParseError
from xcopilot.style.passport import StylePassport
from xcopilot.style.render_md import CASING_MD, LEVEL_MD, export_md, render
from xcopilot.style.service import PassportVersion, StyleService, list_rules

BAN = re.compile(r"^\s*добавь запрет\s*:\s*(.+?)\s*$", re.I | re.S)
UNRULE = re.compile(r"^\s*убери правило\s+(\d+)\s*$", re.I)
TOGGLES = (
    ("casing", "Регистр"),
    ("emoji", "Эмодзи"),
    ("hashtags", "Хэштеги"),
    ("links", "Ссылки"),
)
PAGE = 8
FILE_MAX = 200_000

Rows = list[list[tuple[str, str]]]


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows
        ]
    )


def rules_summary(p: StylePassport) -> str:
    """Жёсткие правила одной строкой: «строчные буквы, без эмодзи, … до 280 знаков»."""
    r = p.hard_rules
    parts: list[str] = []
    if r.casing == "lower":
        parts.append("строчные буквы")
    for value, none, rare in (
        (r.emoji, "без эмодзи", "эмодзи редко"),
        (r.hashtags, "без хэштегов", "хэштеги редко"),
        (r.links, "без ссылок", "ссылки редко"),
    ):
        if value == "none":
            parts.append(none)
        elif value == "rare":
            parts.append(rare)
    parts.append(f"до {r.max_len} знаков")
    if r.banned_phrases:
        parts.append(f"запретов: {len(r.banned_phrases)}")
    return ", ".join(parts)


def next_value(name: str, value: str) -> str:
    """Что поставит нажатие: регистр любой ↔ строчные; уровни свободно → нет → свободно."""
    if name == "casing":
        return "any" if value == "lower" else "lower"
    return "free" if value == "none" else "none"


def toggle_label(name: str, title: str, value: str) -> str:
    """«Регистр: любой ▸ строчные» — сейчас и после нажатия."""
    names = CASING_MD if name == "casing" else LEVEL_MD
    return f"{title}: {names[value]} ▸ {names[next_value(name, value)]}"


def _home_kb(has_passport: bool) -> InlineKeyboardMarkup:
    if not has_passport:
        return _kb(
            [(texts.STYLE_BTN_UPLOAD, "st:up"), (texts.STYLE_BTN_REBUILD, "st:rebuild")],
        )
    return _kb(
        [(texts.STYLE_BTN_DOWNLOAD, "st:md"), (texts.STYLE_BTN_UPLOAD, "st:up")],
        [(texts.STYLE_BTN_REBUILD, "st:rebuild"), (texts.STYLE_BTN_LIST, "st:pp:0")],
        [(texts.STYLE_BTN_HARD, "st:hr")],
    )


def _activate_kb(version: int) -> InlineKeyboardMarkup:
    return _kb([(texts.STYLE_BTN_ACTIVATE, f"st:act:{version}")])


def _head(v: PassportVersion) -> str:
    author = texts.style_author(v.created_by, v.passport.origin, long=True)
    return texts.style_head(v.version, v.status, f"{v.created_at:%d.%m}", author, v.passport.label)


def _summary(v: PassportVersion) -> str:
    p = v.passport
    owner_rules = len([line for line in p.owner_rules.splitlines() if line.strip()])
    details = texts.style_details(
        p.tone, rules_summary(p), owner_rules, len(p.examples), rough=p.rough
    )
    return f"{_head(v)}\n{details}"


def _list_button(v: PassportVersion) -> str:
    text = f"v{v.version}" + (" ✅" if v.status == "active" else "")
    if v.passport.label:
        text += f" · {v.passport.label}"
    return text[:60]


def _list_line(v: PassportVersion) -> str:
    author = texts.style_author(v.created_by, v.passport.origin)
    line = f"v{v.version}" + (" ✅" if v.status == "active" else "")
    line += f" · {v.created_at:%d.%m} · {author}"
    return line + (f" · «{v.passport.label}»" if v.passport.label else "")


def _hard_screen(v: PassportVersion, extra: Rows | None = None) -> tuple[str, InlineKeyboardMarkup]:
    rules = v.passport.hard_rules
    numbered = [f"{i}. {text}" for i, (_, text) in enumerate(list_rules(v.passport), 1)]
    text = texts.style_hard_rules(v.version, numbered, rules.max_len, v.status)
    rows: Rows = [
        [(toggle_label(name, title, getattr(rules, name)), f"st:tg:{name}")]
        for name, title in TOGGLES
    ]
    rows += extra or []
    rows.append([(texts.STYLE_BTN_HOME, "st:home")])
    return text, _kb(*rows)


def _int(data: str | None, prefix: str) -> int | None:
    try:
        return int((data or "").removeprefix(prefix))
    except ValueError:
        return None


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="style")

    async def _ready(
        message: Message, deps: BotDeps, acc: AccountCtx | None
    ) -> tuple[StyleService, UUID] | None:
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return None
        if deps.style is None:
            await message.answer(texts.STYLE_UNAVAILABLE)
            return None
        return deps.style, acc.id

    async def _current(service: StyleService, account_id: UUID) -> PassportVersion | None:
        """Активный паспорт, иначе последняя версия."""
        active = await service.get_active(account_id)
        if active is not None:
            return active
        versions = await service.list_versions(account_id)
        return versions[-1] if versions else None

    async def _home(message: Message, service: StyleService, account_id: UUID) -> None:
        active = await service.get_active(account_id)
        if active is not None:
            await message.answer(_summary(active), reply_markup=_home_kb(True))
            return
        versions = await service.list_versions(account_id)
        if versions:
            await message.answer(
                texts.style_draft_only(versions[-1].version), reply_markup=_home_kb(True)
            )
        else:
            await message.answer(texts.STYLE_NONE, reply_markup=_home_kb(False))

    async def _send_file(message: Message, v: PassportVersion) -> None:
        data = export_md(v.passport, v.version).encode()
        await message.answer_document(
            BufferedInputFile(data, filename=f"style-passport-v{v.version}.md")
        )

    @router.callback_query(F.data == "menu:style")
    async def on_menu(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message
        if not isinstance(message, Message):
            return
        await rec("menu.style")
        ready = await _ready(message, deps, acc)
        if ready:
            await _home(message, *ready)

    async def _cb(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, action: str
    ) -> tuple[Message, StyleService, UUID] | None:
        await query.answer()
        message = query.message
        if not isinstance(message, Message):
            return None
        await rec(f"style.{action}")
        ready = await _ready(message, deps, acc)
        return (message, *ready) if ready else None

    @router.callback_query(F.data == "st:home")
    async def on_home(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "home")
        if ctx is not None:
            await _home(*ctx)

    # --- жёсткие правила ------------------------------------------------------------------

    @router.callback_query(F.data.in_({"st:hr", "st:rules"}))
    async def on_rules(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "rules")
        if ctx is None:
            return
        message, service, account_id = ctx
        current = await service.get_working(account_id)
        if current is None:
            await message.answer(texts.STYLE_NONE, reply_markup=_home_kb(False))
            return
        extra: Rows = []
        if current.status != "active":
            extra.append(
                [(f"{texts.STYLE_BTN_ACTIVATE} v{current.version}", f"st:act:{current.version}")]
            )
        text, kb = _hard_screen(current, extra)
        await message.answer(text, reply_markup=kb)

    @router.callback_query(F.data.startswith("st:tg:"))
    async def on_toggle(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "toggle")
        if ctx is None:
            return
        message, service, account_id = ctx
        name = (query.data or "").removeprefix("st:tg:")
        current = await service.get_working(account_id)
        titles = dict(TOGGLES)
        if name not in titles or current is None:
            await message.answer(texts.STYLE_NONE)
            return
        value = next_value(name, getattr(current.passport.hard_rules, name))
        created = await service.patch_hard_rule(account_id, name, value, user_id=user_id)
        shown = (CASING_MD if name == "casing" else LEVEL_MD)[value]
        _, kb = _hard_screen(
            created,
            [[(f"{texts.STYLE_BTN_ACTIVATE} v{created.version}", f"st:act:{created.version}")]],
        )
        await message.answer(
            texts.style_saved(created.version, f"{titles[name]}: {shown}."), reply_markup=kb
        )

    # --- примеры, откат, активация (старые кнопки работают) -------------------------------

    @router.callback_query(F.data == "st:ex")
    async def on_examples(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "examples")
        if ctx is None:
            return
        message, service, account_id = ctx
        current = await _current(service, account_id)
        if current is None or not current.passport.examples:
            await message.answer(texts.STYLE_NO_EXAMPLES)
            return
        lines = [f"{i}. {e.text}" for i, e in enumerate(current.passport.examples[:8], 1)]
        if current.passport.anti_examples:
            lines += ["", "Так не пишу:"] + [f"– {a}" for a in current.passport.anti_examples[:3]]
        await message.answer("\n".join(lines)[:4000])

    @router.callback_query(F.data == "st:rb")
    async def on_rollback(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "rollback")
        if ctx is None:
            return
        message, service, account_id = ctx
        try:
            restored = await service.rollback(account_id, user_id=user_id)
        except NothingToRollback:
            await message.answer(texts.STYLE_NOTHING_TO_ROLLBACK)
            return
        await message.answer(texts.style_rolled_back(restored.version))

    @router.callback_query(F.data.startswith("st:act:"))
    async def on_activate(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "activate")
        if ctx is None:
            return
        message, service, account_id = ctx
        try:
            version = int((query.data or "").removeprefix("st:act:"))
            done = await service.activate(account_id, version, user_id=user_id)
        except (ValueError, NotFound):
            await message.answer(texts.STYLE_NONE)
            return
        await message.answer(texts.style_activated(done.version))

    @router.callback_query(F.data == "st:keep")
    async def on_keep(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "keep")
        if ctx is None:
            return
        message, service, account_id = ctx
        active = await service.get_active(account_id)
        versions = await service.list_versions(account_id)
        if active is not None and versions and versions[-1].version != active.version:
            await message.answer(texts.style_kept(active.version, versions[-1].version))
        elif active is not None:
            await message.answer(texts.style_activated(active.version))
        else:
            await message.answer(texts.STYLE_NONE)

    # --- пересборка -----------------------------------------------------------------------

    @router.callback_query(F.data == "st:rebuild")
    async def on_rebuild(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "rebuild")
        if ctx is None:
            return
        message, service, account_id = ctx
        await message.answer(texts.STYLE_BUILDING)
        try:
            rebuilt = await service.rebuild_merged(account_id, user_id=user_id)
        except NoOwnPosts:
            await message.answer(texts.STYLE_NO_POSTS)
            return
        version = rebuilt.version.version
        diff = "\n".join(f"• {line}" for line in rebuilt.diff) or "• без изменений"
        await message.answer(
            texts.style_rebuilt(version, diff, rebuilt.proposals),
            reply_markup=_kb(
                [(texts.STYLE_BTN_APPLY, f"st:act:{version}"), (texts.CANCEL_BUTTON, "st:keep")]
            ),
        )

    # --- скачать и загрузить файл ---------------------------------------------------------

    @router.callback_query(F.data == "st:md")
    async def on_export(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "export")
        if ctx is None:
            return
        message, service, account_id = ctx
        current = await _current(service, account_id)
        if current is None:
            await message.answer(texts.STYLE_NONE)
            return
        await _send_file(message, current)

    @router.callback_query(F.data == "st:up")
    async def on_upload(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
        user_id: UUID,
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "upload")
        if ctx is None:
            return
        message, service, account_id = ctx
        if not await service.is_owner(account_id, user_id):
            await message.answer(texts.STYLE_UPLOAD_OWNER_ONLY)
            return
        await state.set_state(StyleFlow.upload)
        await state.set_data({})
        await message.answer(texts.STYLE_UPLOAD_ASK, reply_markup=keyboards.cancel())

    async def _file_text(message: Message, deps: BotDeps) -> str | None:
        document = message.document
        if document is None:
            return message.text or ""
        if (document.file_size or 0) > FILE_MAX:
            await message.answer(texts.STYLE_UPLOAD_TOO_BIG, reply_markup=keyboards.cancel())
            return None
        buf = io.BytesIO()
        await deps.bot.download(document.file_id, destination=buf)
        try:
            return buf.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            await message.answer(texts.STYLE_UPLOAD_NOT_TEXT, reply_markup=keyboards.cancel())
            return None

    @router.message(
        StateFilter(StyleFlow.upload, StyleFlow.confirm),
        F.document | (F.text & ~F.text.startswith("/")),
    )
    async def on_upload_file(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        await rec("style.upload_file")
        ready = await _ready(message, deps, acc)
        if ready is None:
            return
        service, account_id = ready
        text = await _file_text(message, deps)
        if text is None:
            return
        if len(text.encode()) > FILE_MAX:
            await message.answer(texts.STYLE_UPLOAD_TOO_BIG, reply_markup=keyboards.cancel())
            return
        try:
            preview = await service.preview_file(account_id, text)
        except PassportParseError as exc:
            await state.set_state(StyleFlow.upload)
            await message.answer(texts.style_file_error(str(exc)), reply_markup=keyboards.cancel())
            return
        await state.set_state(StyleFlow.confirm)
        await state.set_data({"md": text})
        diff = "\n".join(f"• {line}" for line in preview.diff) or "• без изменений"
        await message.answer(
            texts.style_file_preview(preview.base_version, diff, preview.warnings),
            reply_markup=_kb(
                [(texts.STYLE_BTN_APPLY, "st:upok"), (texts.CANCEL_BUTTON, CANCEL_CB)]
            ),
        )

    @router.callback_query(F.data == "st:upok")
    async def on_upload_apply(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
        user_id: UUID,
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "upload_apply")
        if ctx is None:
            return
        message, service, account_id = ctx
        data: dict[str, Any] = await state.get_data()
        md = data.get("md")
        if await state.get_state() != StyleFlow.confirm.state or not isinstance(md, str):
            await message.answer(texts.STYLE_UPLOAD_GONE)
            return
        if not await service.is_owner(account_id, user_id):
            await message.answer(texts.STYLE_UPLOAD_OWNER_ONLY)
            return
        try:
            applied = await service.apply_file(account_id, md, user_id=user_id)
        except PassportParseError as exc:
            await message.answer(texts.style_file_error(str(exc)))
            return
        finally:
            await state.clear()
        await message.answer(texts.style_file_applied(applied.version))

    # --- «🕘 Паспорта» ---------------------------------------------------------------------

    async def _show_list(
        message: Message, service: StyleService, account_id: UUID, page: int
    ) -> None:
        versions = list(reversed(await service.list_versions(account_id)))
        if not versions:
            await message.answer(texts.STYLE_NONE, reply_markup=_home_kb(False))
            return
        pages = (len(versions) + PAGE - 1) // PAGE
        page = min(max(page, 0), pages - 1)
        chunk = versions[page * PAGE : (page + 1) * PAGE]
        rows: Rows = [[(_list_button(v), f"st:pv:{v.version}")] for v in chunk]
        nav: list[tuple[str, str]] = []
        if page > 0:
            nav.append(("◀️", f"st:pp:{page - 1}"))
        if page < pages - 1:
            nav.append(("▶️", f"st:pp:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([(texts.STYLE_BTN_HOME, "st:home")])
        await message.answer(
            texts.style_list(page, pages, [_list_line(v) for v in chunk]), reply_markup=_kb(*rows)
        )

    @router.callback_query(F.data.startswith("st:pp:") | (F.data == "st:hist"))
    async def on_list(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "history")
        if ctx is None:
            return
        await _show_list(*ctx, _int(query.data, "st:pp:") or 0)

    async def _version(
        message: Message, service: StyleService, account_id: UUID, number: int | None
    ) -> PassportVersion | None:
        found = None if number is None else await service.get(account_id, number)
        if found is None:
            await message.answer(texts.STYLE_NO_VERSION)
        return found

    async def _show_card(message: Message, v: PassportVersion) -> None:
        n = v.version
        rows: Rows = []
        if v.status != "active":
            rows.append(
                [(texts.STYLE_BTN_SHOW, f"st:show:{n}"), (texts.STYLE_BTN_COMPARE, f"st:cmp:{n}")]
            )
            rows.append(
                [
                    (texts.STYLE_BTN_ACTIVATE, f"st:act:{n}"),
                    (texts.STYLE_BTN_DOWNLOAD, f"st:dl:{n}"),
                ]
            )
        else:
            rows.append(
                [(texts.STYLE_BTN_SHOW, f"st:show:{n}"), (texts.STYLE_BTN_DOWNLOAD, f"st:dl:{n}")]
            )
        page = 0
        rows.append(
            [(texts.STYLE_BTN_NAME, f"st:nm:{n}"), (texts.STYLE_BTN_BACK_LIST, f"st:pp:{page}")]
        )
        await message.answer(_summary(v), reply_markup=_kb(*rows))

    @router.callback_query(F.data.startswith("st:pv:"))
    async def on_card(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "card")
        if ctx is None:
            return
        message, service, account_id = ctx
        v = await _version(message, service, account_id, _int(query.data, "st:pv:"))
        if v is not None:
            await _show_card(message, v)

    @router.callback_query(F.data.startswith("st:show:"))
    async def on_show(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "show")
        if ctx is None:
            return
        message, service, account_id = ctx
        v = await _version(message, service, account_id, _int(query.data, "st:show:"))
        if v is not None:
            for part in render(v.passport, v.version):
                await message.answer(part)

    @router.callback_query(F.data.startswith("st:dl:"))
    async def on_download(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "download")
        if ctx is None:
            return
        message, service, account_id = ctx
        v = await _version(message, service, account_id, _int(query.data, "st:dl:"))
        if v is not None:
            await _send_file(message, v)

    @router.callback_query(F.data.startswith("st:cmp:"))
    async def on_compare(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "compare")
        if ctx is None:
            return
        message, service, account_id = ctx
        v = await _version(message, service, account_id, _int(query.data, "st:cmp:"))
        if v is None:
            return
        active = await service.get_active(account_id)
        if active is not None and active.version == v.version:
            await message.answer(texts.STYLE_SAME_AS_ACTIVE)
            return
        text = (
            texts.style_compare(v.version, active.version, diff_text(active.passport, v.passport))
            if active is not None
            else diff_text(None, v.passport)
        )
        await message.answer(text, reply_markup=_activate_kb(v.version))

    @router.callback_query(F.data.startswith("st:nm:"))
    async def on_name(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "name")
        if ctx is None:
            return
        message, service, account_id = ctx
        v = await _version(message, service, account_id, _int(query.data, "st:nm:"))
        if v is None:
            return
        await state.set_state(StyleFlow.name)
        await state.set_data({"version": v.version})
        await message.answer(texts.style_name_ask(v.version), reply_markup=keyboards.cancel())

    @router.message(StyleFlow.name, F.text, ~F.text.startswith("/"))
    async def on_name_text(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
        user_id: UUID,
    ) -> None:
        await rec("style.name_text")
        ready = await _ready(message, deps, acc)
        if ready is None:
            return
        service, account_id = ready
        data: dict[str, Any] = await state.get_data()
        version = int(data.get("version") or 0)
        try:
            named = await service.rename(account_id, version, message.text or "", user_id=user_id)
        except ValueError:
            await message.answer(texts.STYLE_NAME_TOO_LONG, reply_markup=keyboards.cancel())
            return
        except NotFound:
            await state.clear()
            await message.answer(texts.STYLE_NO_VERSION)
            return
        await state.clear()
        await message.answer(texts.style_named(named.version, named.passport.label))
        await _show_card(message, named)

    # --- правила текстом ------------------------------------------------------------------

    @router.message(StateFilter(None), F.text.regexp(BAN))
    async def on_ban(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        await rec("style.ban")
        ready = await _ready(message, deps, acc)
        match = BAN.match(message.text or "")
        if ready is None or match is None:
            return
        service, account_id = ready
        phrase = match.group(1)
        created = await service.add_banned_phrase(account_id, phrase, user_id=user_id)
        if created is None:
            await message.answer(f"Запрет «{phrase}» уже есть.")
            return
        await message.answer(
            texts.style_saved(created.version, f"Добавил запрет «{phrase}»."),
            reply_markup=_activate_kb(created.version),
        )

    @router.message(StateFilter(None), F.text.regexp(UNRULE))
    async def on_unrule(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        await rec("style.unrule")
        ready = await _ready(message, deps, acc)
        match = UNRULE.match(message.text or "")
        if ready is None or match is None:
            return
        service, account_id = ready
        number = int(match.group(1))
        try:
            created = await service.remove_rule(account_id, number, user_id=user_id)
        except UnknownRule:
            await message.answer(f"Нет правила {number}.")
            return
        await message.answer(
            texts.style_saved(created.version, f"Убрал правило {number}."),
            reply_markup=_activate_kb(created.version),
        )

    return router
