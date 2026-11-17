"""«Мозг» в Telegram (этап 9C): /brain, сегменты, карточки, действия владельца, сводка, откат.

Данные кнопок — `bn:<действие>[:<аргументы>]` (см. `keyboards`). Читать базу может любой
участник аккаунта, менять — только владелец (`owner.NotOwner` → «только владелец»).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Literal, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from xcopilot.bot import brain_view as view
from xcopilot.bot import keyboards
from xcopilot.bot import texts as bot_texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.states import BrainFlow
from xcopilot.brain import digest, export, owner, rollback, segments
from xcopilot.brain.context import BrainContext
from xcopilot.brain.jobs import BrainDeps, enqueue_extract
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.uow import DbSession
from xcopilot.errors import NotFound
from xcopilot.learning.passport_sync import apply_confirmed_voice_item

T = TypeVar("T")


async def _t(awaitable: Awaitable[object]) -> bool:
    """Дождаться действия без результата и вернуть True (для `_guarded`)."""
    await awaitable
    return True


def brain_deps(deps: BotDeps) -> BrainDeps:
    """Зависимости «Мозга» бота; не заданы — без моделей и эмбеддера (правка без вектора)."""
    return deps.brain or BrainDeps(uow_factory=deps.uow_factory, clock=deps.clock)


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="brain")

    def _msg(query: CallbackQuery) -> Message | None:
        return query.message if isinstance(query.message, Message) else None

    async def _run(
        deps: BotDeps,
        acc: AccountCtx,
        fn: Callable[[BrainContext, DbSession], Awaitable[T]],
    ) -> T:
        bd = brain_deps(deps)
        async with deps.uow_factory() as uow:
            return await fn(bd.ctx(uow.session, acc.id), uow.session)

    async def _guarded(
        message: Message,
        deps: BotDeps,
        acc: AccountCtx,
        fn: Callable[[BrainContext, DbSession], Awaitable[T]],
        fail: str = "Не получилось",
    ) -> tuple[bool, T | None]:
        """Выполнить действие → (получилось ли, результат); отказ — понятным сообщением."""
        try:
            return True, await _run(deps, acc, fn)
        except owner.NotOwner:
            await message.answer(view.NOT_OWNER)
        except owner.InvalidItem as exc:
            await message.answer(f"{fail}: {exc}.")
        except NotFound:
            await message.answer("Такого знания уже нет.")
        return False, None

    # ── показ ─────────────────────────────────────────────────────────────────────────

    async def _show_menu(message: Message, deps: BotDeps, acc: AccountCtx) -> None:
        async def load(
            _: BrainContext, s: DbSession
        ) -> tuple[segments.BrainCounts, list[segments.SegmentItem]]:
            await segments.ensure_defaults(s, acc.id)
            return await segments.counts(s, acc.id), await segments.class_a_pending(s, acc.id)

        counts, pending = await _run(deps, acc, load)
        text, keyboard = view.render_menu(counts)
        await message.answer(text, reply_markup=keyboard)
        for item in pending:  # знания класса A — отдельными сообщениями, ждут ответа
            text, keyboard = view.render_class_a(item)
            await message.answer(text, reply_markup=keyboard)

    async def _show_segment(
        message: Message, deps: BotDeps, acc: AccountCtx, key: str, page: int
    ) -> None:
        async def load(_: BrainContext, s: DbSession) -> tuple[str, segments.Page]:
            counts = await segments.counts(s, acc.id)
            title = next((c.title for c in counts.segments if c.key == key), key)
            return title, await segments.list_items(s, acc.id, key, page)

        try:
            title, data = await _run(deps, acc, load)
        except NotFound:
            await message.answer(view.NOTHING_FOUND)
            return
        label = keyboards.BRAIN_SEGMENT_LABELS.get(key)
        icon = label.split(" ", 1)[0] if label else "🎯"
        text, keyboard = view.render_segment(f"{icon} {title}", key, data)
        await message.answer(text, reply_markup=keyboard)

    async def _show_card(message: Message, deps: BotDeps, acc: AccountCtx, item_id: UUID) -> None:
        try:
            card = await _run(deps, acc, lambda _, s: segments.item_view(s, acc.id, item_id))
        except NotFound:
            await message.answer("Такого знания уже нет.")
            return
        text, keyboard = view.render_card(card)
        await message.answer(text, reply_markup=keyboard)

    async def _show_evidence(
        message: Message, deps: BotDeps, acc: AccountCtx, item_id: UUID
    ) -> None:
        async def load(
            _: BrainContext, s: DbSession
        ) -> tuple[segments.ItemView, list[segments.EvidenceRow]]:
            return (
                await segments.item_view(s, acc.id, item_id),
                await segments.evidence_list(s, acc.id, item_id),
            )

        try:
            card, rows = await _run(deps, acc, load)
        except NotFound:
            await message.answer("Такого знания уже нет.")
            return
        text, keyboard = view.render_evidence(card, rows)
        await message.answer(text, reply_markup=keyboard)

    async def _show_conflicts(message: Message, deps: BotDeps, acc: AccountCtx) -> None:
        async def load(ctx: BrainContext, s: DbSession) -> list[digest.DigestConflict]:
            return (await digest.weekly_digest(ctx, date.today())).conflicts

        conflicts = await _run(deps, acc, load)
        if not conflicts:
            await message.answer("Открытых конфликтов нет.")
        for c in conflicts:
            text, keyboard = view.render_conflict(c.first, c.second, c.link_id)
            await message.answer(text, reply_markup=keyboard)

    async def _show_digest(
        message: Message, deps: BotDeps, acc: AccountCtx, week: date
    ) -> digest.Digest:
        result = await _run(deps, acc, lambda ctx, _: digest.weekly_digest(ctx, week))
        text, keyboard = view.render_digest(result)
        await message.answer(text, reply_markup=keyboard)
        return result

    def _today(deps: BotDeps, acc: AccountCtx) -> date:
        return deps.clock.now().astimezone(ZoneInfo(acc.tz)).date()

    async def _preview_rollback(
        message: Message, deps: BotDeps, acc: AccountCtx, day: date
    ) -> None:
        to_ts = datetime.combine(day, time(23, 59, 59), tzinfo=ZoneInfo(acc.tz))
        plan = await _run(deps, acc, lambda ctx, _: rollback.plan(ctx, to_ts))
        text, keyboard = view.render_rollback_preview(
            day, len(plan.created_items), len(plan.changed_items), plan.evidence_added
        )
        await message.answer(text, reply_markup=keyboard)

    async def _send_export(message: Message, deps: BotDeps, acc: AccountCtx) -> None:
        async def load(_: BrainContext, s: DbSession) -> list[str]:
            return [line async for line in export.export_full(s, acc.id)]

        lines = await _run(deps, acc, load)
        if not lines:
            await message.answer(view.NOTHING_FOUND)
            return
        data = ("\n".join(lines) + "\n").encode()
        await message.answer_document(
            BufferedInputFile(data, filename="brain.jsonl"),
            caption=f"Вся база знаний: {len(lines)} шт.",
        )

    # ── /brain ────────────────────────────────────────────────────────────────────────

    @router.message(F.text == keyboards.BTN_BRAIN)
    async def on_brain_reply_button(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("brain")
        if acc is None:
            await message.answer(bot_texts.NO_ACCOUNT)
            return
        await _show_menu(message, deps, acc)

    @router.message(Command("brain"))
    async def cmd_brain(
        message: Message,
        command: CommandObject,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        await rec("brain")
        if acc is None:
            await message.answer(bot_texts.NO_ACCOUNT)
            return
        sub, _, rest = (command.args or "").strip().partition(" ")
        sub, rest = sub.lower(), rest.strip()
        if sub == "":
            await _show_menu(message, deps, acc)
        elif sub == "add":
            ok, _created = await _guarded(
                message,
                deps,
                acc,
                lambda ctx, _: owner.add_manual(ctx, user_id, rest),
                fail="Не добавил",
            )
            if ok:
                await message.answer(f"Добавил: «{rest}». Теперь оно в работе.")
        elif sub == "search":
            found = await _run(deps, acc, lambda _, s: segments.search(s, acc.id, rest))
            text, keyboard = view.render_search(rest, found)
            await message.answer(text, reply_markup=keyboard)
        elif sub == "week":
            await _show_digest(message, deps, acc, _today(deps, acc) - timedelta(days=6))
        elif sub == "undo":
            if not rest:
                await message.answer(
                    view.ROLLBACK_PICK, reply_markup=keyboards.brain_rollback_dates()
                )
                return
            try:
                day = date.fromisoformat(rest.split()[0])
            except ValueError:
                await message.answer(
                    view.ROLLBACK_PICK, reply_markup=keyboards.brain_rollback_dates()
                )
                return
            await _preview_rollback(message, deps, acc, day)
        elif sub == "settings":
            mode = await _run(deps, acc, lambda ctx, _: owner.autonomy(ctx))
            text, keyboard = view.render_settings(mode)
            await message.answer(text, reply_markup=keyboard)
        elif sub == "export":
            await _send_export(message, deps, acc)
        else:
            await message.answer(
                "Команды: /brain · /brain add <текст> · /brain search <текст> · /brain week · "
                "/brain undo · /brain settings"
            )

    # ── кнопки ────────────────────────────────────────────────────────────────────────

    @router.callback_query(F.data.startswith("bn:"))
    async def on_brain_button(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        await query.answer()
        message = _msg(query)
        if message is None:
            return
        if acc is None:
            await message.answer(bot_texts.NO_ACCOUNT)
            return
        parts = (query.data or "").split(":")
        action, args = parts[1], parts[2:]
        await rec(f"brain.{action}")

        def uid(n: int = 0) -> UUID | None:
            try:
                return UUID(args[n])
            except (IndexError, ValueError):
                return None

        item_id = uid()
        if action == "menu":
            await _show_menu(message, deps, acc)
        elif action == "seg" and len(args) == 2 and args[1].isdigit():
            await _show_segment(message, deps, acc, args[0], int(args[1]))
        elif action == "goals":
            counts = await _run(deps, acc, lambda _, s: segments.counts(s, acc.id))
            await message.answer(
                "🎯 По цели",
                reply_markup=keyboards.brain_goals({c.key: c.count for c in counts.segments}),
            )
        elif action == "item" and item_id:
            await _show_card(message, deps, acc, item_id)
        elif action == "ev" and item_id:
            await _show_evidence(message, deps, acc, item_id)
        elif action == "evrm" and item_id:
            ok, _r = await _guarded(
                message,
                deps,
                acc,
                lambda ctx, _: _t(owner.remove_evidence(ctx, item_id, user_id)),
            )
            if not ok:
                return
            await message.answer("Убрал доказательство, веса пересчитал. Материал остался в базе.")
        elif action in {"ok", "pin", "nm", "delyes", "aok", "ano", "keep"} and item_id:
            await _item_action(message, deps, acc, user_id, action, item_id)
        elif action == "del" and item_id:
            await message.answer(
                view.DELETE_ASK, reply_markup=keyboards.brain_delete_confirm(item_id)
            )
        elif action == "delno":
            await message.answer("Оставил знание как есть.")
        elif action in {"edit", "aedit"} and item_id:
            try:
                await _run(deps, acc, lambda ctx, _: owner.require_owner(ctx, user_id))
            except owner.NotOwner:
                await message.answer(view.NOT_OWNER)
                return
            await state.set_state(BrainFlow.edit)
            await state.update_data(item_id=str(item_id))
            await message.answer(view.EDIT_ASK, reply_markup=keyboards.cancel())
        elif action == "add" and args and args[0] in keyboards.BRAIN_SEGMENT_FORMATS:
            try:
                await _run(deps, acc, lambda ctx, _: owner.require_owner(ctx, user_id))
            except owner.NotOwner:
                await message.answer(view.NOT_OWNER)
                return
            await state.set_state(BrainFlow.add)
            await state.update_data(segment=args[0])
            await message.answer(
                view.add_rule_ask(keyboards.BRAIN_SEGMENT_LABELS[args[0]]),
                reply_markup=keyboards.cancel(),
            )
        elif action == "where" and item_id:
            await message.answer(view.WHERE_ASK, reply_markup=keyboards.brain_where(item_id))
        elif action == "wh" and item_id and len(args) == 2 and args[1] in view.WHERE_CHOICES:
            fmt = args[1]
            ok, _r = await _guarded(
                message,
                deps,
                acc,
                lambda ctx, _: _t(
                    owner.set_formats(ctx, item_id, user_id, None if fmt == "all" else [fmt])
                ),
            )
            if not ok:
                return
            await message.answer(view.where_set(fmt))
        elif action == "cfl":
            await _show_conflicts(message, deps, acc)
        elif (
            action == "cf"
            and item_id
            and len(args) == 2
            and args[1] in {"first", "second", "scope"}
        ):
            how: Literal["first", "second", "scope"] = args[1]  # type: ignore[assignment]
            ok, _r = await _guarded(
                message,
                deps,
                acc,
                lambda ctx, _: _t(owner.resolve_conflict(ctx, item_id, user_id, how)),
            )
            if not ok:
                return
            await message.answer(
                "Решил: "
                + {
                    "first": "оставляю первое",
                    "second": "оставляю второе",
                    "scope": "оба, развёл по целям",
                }[how]
                + "."
            )
        elif action == "w":
            await _show_digest(message, deps, acc, _today(deps, acc) - timedelta(days=6))
        elif action in {"dall", "done"} and args:
            await _digest_batch(message, deps, acc, user_id, action, args[0])
        elif action == "drev" and item_id:
            ok, _r = await _guarded(
                message,
                deps,
                acc,
                lambda ctx, _: _t(digest.revert_auto(ctx, item_id, user_id)),
            )
            if not ok:
                return
            await message.answer("Убрал из испытания: знание вернулось в кандидаты.")
        elif action == "learn":
            await _learn_now(message, deps, acc, user_id)
        elif action == "search":
            await message.answer(view.SEARCH_HINT)
        elif action == "export":
            await _send_export(message, deps, acc)
        elif action == "rb":
            await message.answer(view.ROLLBACK_PICK, reply_markup=keyboards.brain_rollback_dates())
        elif action == "rbd" and args and args[0].isdigit():
            await _preview_rollback(
                message, deps, acc, _today(deps, acc) - timedelta(days=int(args[0]))
            )
        elif action == "rbok" and args:
            await _do_rollback(message, deps, acc, user_id, args[0])
        elif action == "rbno":
            await message.answer("Хорошо, базу не менял.")
        elif action == "rbundo" and item_id:
            ok, _r = await _guarded(
                message, deps, acc, lambda ctx, _: _t(rollback.undo(ctx, item_id, user_id))
            )
            if not ok:
                return
            await message.answer("Откат отменён: знания и доказательства вернулись.")
        elif action == "set" and args and args[0] in {"auto", "manual"}:
            mode: Literal["auto", "manual"] = args[0]  # type: ignore[assignment]
            ok, _r = await _guarded(
                message, deps, acc, lambda ctx, _: _t(owner.set_autonomy(ctx, user_id, mode))
            )
            if not ok:
                return
            text, keyboard = view.render_settings(mode)
            await message.answer(text, reply_markup=keyboard)

    async def _learn_now(message: Message, deps: BotDeps, acc: AccountCtx, user_id: UUID) -> None:
        """«Обучить сейчас»: brain.extract с force — все необработанные разборы."""

        async def go(ctx: BrainContext, s: DbSession) -> tuple[int, bool]:
            await owner.require_owner(ctx, user_id)
            n = len(await BrainRepo(s, acc.id).pending_materials())
            if n == 0:
                return 0, False
            return n, await enqueue_extract(s, acc.id, force=True) is not None

        ok, got = await _guarded(message, deps, acc, go)
        if not ok or got is None:
            return
        n, queued = got
        if n == 0:
            await message.answer("Необработанных разборов нет: учиться пока не на чем.")
        elif not queued:
            await message.answer("Уже учусь: итог пришлю сообщением.")
        else:
            word = "материале" if n % 10 == 1 and n % 100 != 11 else "материалах"
            await message.answer(f"Учусь на {n} {word}…")

    async def _item_action(
        message: Message, deps: BotDeps, acc: AccountCtx, user_id: UUID, action: str, item_id: UUID
    ) -> None:
        async def go(ctx: BrainContext, _: DbSession) -> str:
            if action in {"ok", "aok", "keep"}:
                await owner.confirm(ctx, item_id, user_id)
                version = await apply_confirmed_voice_item(ctx, item_id, user_id)  # этап 13
                if version is not None:
                    return f"Взял в работу. Запрет добавлен в паспорт (версия {version})."
                return "Взял в работу."
            if action == "pin":
                await owner.pin(ctx, item_id, user_id)
                return "Закрепил: оно всегда будет в черновиках."
            if action == "nm":
                await owner.disable(ctx, item_id, user_id, not_mine=True)
                return "Понял: знание остаётся в базе, но в твои черновики не идёт."
            await owner.reject(ctx, item_id, user_id, delete=action == "delyes")
            return "Удалил. Похожие 30 дней не предложу." if action == "delyes" else "Отклонил."

        ok, said = await _guarded(message, deps, acc, go)
        if ok and said is not None:
            await message.answer(said)

    async def _digest_batch(
        message: Message, deps: BotDeps, acc: AccountCtx, user_id: UUID, action: str, week_s: str
    ) -> None:
        try:
            week = datetime.strptime(week_s, "%Y%m%d").date()
        except ValueError:
            return

        async def go(ctx: BrainContext, _: DbSession) -> tuple[digest.Digest, int]:
            result = await digest.weekly_digest(ctx, week)
            if action == "dall":
                return result, await digest.accept_all(ctx, user_id, result)
            return result, 0

        ok, got = await _guarded(message, deps, acc, go)
        if not ok or got is None:
            return
        result, accepted = got
        if action == "dall":
            await message.answer(f"Принял: {accepted}. Знания голоса жду отдельным «да».")
            return
        if not result.auto_trials:
            await message.answer("По одному разбирать нечего.")
            return
        trials = [(t.item_id, t.transition_id, t.statement) for t in result.auto_trials]
        lines = [f"{n}. «{t[2]}»" for n, t in enumerate(trials, start=1)]
        await message.answer("\n".join(lines), reply_markup=keyboards.brain_one_by_one(trials))

    async def _do_rollback(
        message: Message, deps: BotDeps, acc: AccountCtx, user_id: UUID, day_s: str
    ) -> None:
        try:
            day = datetime.strptime(day_s, "%Y%m%d").date()
        except ValueError:
            return
        to_ts = datetime.combine(day, time(23, 59, 59), tzinfo=ZoneInfo(acc.tz))

        async def go(ctx: BrainContext, _: DbSession) -> UUID:
            await owner.require_owner(ctx, user_id)
            return await rollback.execute(ctx, await rollback.plan(ctx, to_ts), user_id)

        ok, rid = await _guarded(message, deps, acc, go)
        if ok and rid is not None:
            await message.answer(
                f"⏪ Откатил базу на {view.human_date(day)}.",
                reply_markup=keyboards.brain_rollback_undo(rid),
            )

    # ── правка текстом ────────────────────────────────────────────────────────────────

    @router.message(BrainFlow.edit, F.text, ~F.text.startswith("/"))
    async def on_edit_text(
        message: Message, state: FSMContext, deps: BotDeps, acc: AccountCtx | None, user_id: UUID
    ) -> None:
        if acc is None:
            return
        data = await state.get_data()
        item_id = UUID(str(data["item_id"]))
        statement = (message.text or "").strip()

        async def go(ctx: BrainContext, s: DbSession) -> int:
            card = await segments.item_view(s, acc.id, item_id)
            return await owner.edit(ctx, item_id, user_id, statement, card.item.guidance)

        ok, version = await _guarded(message, deps, acc, go)
        if not ok or version is None:
            return  # причина названа в ответе; состояние остаётся — можно прислать другой текст
        await state.clear()
        await message.answer(f"Сохранил новую версию (v{version}). Доказательства остались.")

    @router.message(BrainFlow.add, F.text, ~F.text.startswith("/"))
    async def on_add_text(
        message: Message, state: FSMContext, deps: BotDeps, acc: AccountCtx | None, user_id: UUID
    ) -> None:
        if acc is None:
            return
        segment = str((await state.get_data()).get("segment", ""))
        fmt = keyboards.BRAIN_SEGMENT_FORMATS.get(segment)
        if fmt is None:
            await state.clear()
            return
        rule = (message.text or "").strip()
        ok, _id = await _guarded(
            message,
            deps,
            acc,
            lambda ctx, _: owner.add_manual(ctx, user_id, rule, formats=[fmt]),
            fail="Не добавил",
        )
        if not ok:
            return  # причина названа в ответе; шаг остаётся — можно прислать другой текст
        await state.clear()
        await message.answer(view.rule_added(keyboards.BRAIN_SEGMENT_LABELS[segment], rule, fmt))
        await _show_segment(message, deps, acc, segment, 1)

    return router
