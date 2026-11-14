"""«🎨 Стиль»: паспорт, правила, примеры, история версий, пересборка, экспорт .md."""

from __future__ import annotations

import re
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.errors import NotFound
from xcopilot.style.errors import NoOwnPosts, NothingToRollback, UnknownRule
from xcopilot.style.passport import StylePassport
from xcopilot.style.render_md import export_md
from xcopilot.style.service import PassportVersion, StyleService, describe_change, list_rules

BAN = re.compile(r"^\s*добавь запрет\s*:\s*(.+?)\s*$", re.I | re.S)
UNRULE = re.compile(r"^\s*убери правило\s+(\d+)\s*$", re.I)
TOGGLES = (
    ("casing", "Строчные"),
    ("emoji", "Эмодзи"),
    ("hashtags", "Хэштеги"),
    ("links", "Ссылки"),
)


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


def _home_kb(has_passport: bool) -> InlineKeyboardMarkup:
    if not has_passport:
        return _kb([("Пересобрать", "st:rebuild")], [("История версий", "st:hist")])
    return _kb(
        [("Правила", "st:rules"), ("Примеры", "st:ex")],
        [("История версий", "st:hist"), ("Пересобрать", "st:rebuild")],
        [("Скачать .md", "st:md")],
    )


def _activate_kb(version: int, keep: int | None = None) -> InlineKeyboardMarkup:
    row = [("Активировать", f"st:act:{version}")]
    if keep is not None:
        row.append((f"Оставить v{keep}", "st:keep"))
    return _kb(row)


async def _current(service: StyleService, account_id: UUID) -> PassportVersion | None:
    """Активный паспорт, иначе последняя версия."""
    active = await service.get_active(account_id)
    if active is not None:
        return active
    versions = await service.list_versions(account_id)
    return versions[-1] if versions else None


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

    async def _home(message: Message, service: StyleService, account_id: UUID) -> None:
        active = await service.get_active(account_id)
        if active is not None:
            text = texts.style_home(
                active.version,
                active.passport.tone,
                rules_summary(active.passport),
                rough=active.passport.rough,
            )
            await message.answer(text, reply_markup=_home_kb(True))
            return
        versions = await service.list_versions(account_id)
        if versions:
            await message.answer(
                texts.style_draft_only(versions[-1].version), reply_markup=_home_kb(False)
            )
        else:
            await message.answer(texts.STYLE_NONE, reply_markup=_home_kb(False))

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

    @router.callback_query(F.data == "st:rules")
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
        rules = list_rules(current.passport)
        lines = [f"{i}. {text}" for i, (_, text) in enumerate(rules, 1)] or ["—"]
        body = "Жёсткие правила v{}:\n{}\n\n{}".format(
            current.version, "\n".join(lines), texts.STYLE_RULES_HINT
        )
        await message.answer(
            body, reply_markup=_kb(*[[(label, f"st:tg:{name}")] for name, label in TOGGLES])
        )

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
        if name not in dict(TOGGLES) or current is None:
            await message.answer(texts.STYLE_NONE)
            return
        value = getattr(current.passport.hard_rules, name)
        new = (
            ("any" if value == "lower" else "lower")
            if name == "casing"
            else ("free" if value == "none" else "none")
        )
        created = await service.patch_hard_rule(account_id, name, new, user_id=user_id)
        await message.answer(
            texts.style_saved(created.version, "Переключил правило."),
            reply_markup=_activate_kb(created.version),
        )

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

    @router.callback_query(F.data == "st:hist")
    async def on_history(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "history")
        if ctx is None:
            return
        message, service, account_id = ctx
        versions = await service.list_versions(account_id)
        if not versions:
            await message.answer(texts.STYLE_NONE, reply_markup=_home_kb(False))
            return
        lines = [
            f"v{v.version} — {texts.STYLE_VERSION_STATUS[v.status]}, "
            f"{texts.STYLE_AUTHOR[v.created_by]}, {v.created_at:%d.%m %H:%M}"
            for v in reversed(versions[-10:])
        ]
        rows: list[list[tuple[str, str]]] = []
        active = next((v for v in versions if v.status == "active"), None)
        if active is not None:
            older = [v for v in versions if v.status == "archived" and v.version < active.version]
            if older:
                rows.append([(f"Откатить к v{older[-1].version}", "st:rb")])
        for v in [x for x in versions if x.status == "draft"][-3:]:
            rows.append([(f"Активировать v{v.version}", f"st:act:{v.version}")])
        await message.answer("\n".join(lines), reply_markup=_kb(*rows) if rows else None)

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
        if active is not None and versions:
            await message.answer(texts.style_kept(active.version, versions[-1].version))

    @router.callback_query(F.data == "st:rebuild")
    async def on_rebuild(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        ctx = await _cb(query, deps, acc, rec, "rebuild")
        if ctx is None:
            return
        message, service, account_id = ctx
        await message.answer(texts.STYLE_BUILDING)
        before = await service.get_active(account_id)
        try:
            created = await service.rebuild(account_id, user_id=user_id)
        except NoOwnPosts:
            await message.answer(texts.STYLE_NO_POSTS)
            return
        change = describe_change(before.passport if before else None, created.passport)
        await message.answer(
            texts.style_rebuilt(created.version, change),
            reply_markup=_activate_kb(created.version, before.version if before else None),
        )

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
        data = export_md(current.passport, current.version).encode()
        await message.answer_document(
            BufferedInputFile(data, filename=f"style-passport-v{current.version}.md")
        )

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
