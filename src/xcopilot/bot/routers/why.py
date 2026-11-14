"""/why: что попало в последний черновик (отладка ретривала)."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import PromptsRepo, RunsRepo
from xcopilot.retrieval.context_pack import PackIds, rehydrate


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="why")

    @router.message(Command("why"))
    async def cmd_why(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("why")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return

        async with deps.uow_factory() as uow:
            run = await RunsRepo(uow.session, acc.id).latest()
            if run is None:
                await message.answer(texts.WHY_EMPTY)
                return

            ids = PackIds.model_validate(run.context_pack)
            pack = await rehydrate(uow.session, acc.id, ids)

            prompt_label: str | None = None
            if run.prompt_id is not None:
                prompt = await PromptsRepo(uow.session).get(run.prompt_id)
                if prompt is not None:
                    prompt_label = f"{prompt.key} v{prompt.version}"

            passport_version = pack.passport_version
            tokens = pack.tokens
            budget_tokens = pack.budget_tokens
            examples = [c.text for c in pack.examples]
            lessons = [x.text for x in pack.lessons]
            facts = [f.statement for f in pack.facts]
            anti_examples = pack.anti_examples
            references = [c.text for c in pack.references]
            warnings = pack.warnings

        await message.answer(
            texts.why_report(
                passport_version=passport_version,
                prompt_label=prompt_label,
                tokens=tokens,
                budget_tokens=budget_tokens,
                examples=examples,
                lessons=lessons,
                facts=facts,
                anti_examples=anti_examples,
                references=references,
                warnings=warnings,
            )
        )

    return router
