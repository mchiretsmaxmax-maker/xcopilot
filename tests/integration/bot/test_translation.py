"""Русский перевод в карточке (владелец 16.11.2026): cheap-роль, хранение, правка, сбой."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.writer_stand import full_prompt
from xcopilot.bot.deps import BotDeps
from xcopilot.llm.errors import PermanentProviderError

SF = async_sessionmaker[AsyncSession]
EN = [
    "Blobs made rollup fees drop, and I can see it in my own transactions this week",
    "Everyone waits for the upgrade, while I simply moved my wallet to an L2 and forgot gas",
    "Three things I learned about cheap networks: quieter, faster, more boring",
]


def ru_reply(*items: str) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps, translate=True)


async def test_en_account_card_has_ru_translation_below_original(st: CardsStand) -> None:
    st.gs.ws.cheap.push(*[ru_reply(f"Перевод {i}") for i in (1, 2, 3)])

    await st.generate(*EN, lang="en")

    [card] = st.cards()
    assert EN[0] in card.text
    assert "<blockquote expandable>Перевод 1</blockquote>" in card.text
    assert card.text.index(EN[0]) < card.text.index("<blockquote expandable>")
    assert len(st.gs.ws.cheap.calls) == 3  # по одному переводу на вариант, ролью cheap


async def test_translation_prompt_gets_original_text(st: CardsStand) -> None:
    st.gs.ws.cheap.push(*[ru_reply(f"Перевод {i}") for i in (1, 2, 3)])
    run = await st.generate(*EN, lang="en")

    assert len(await st.finals(run)) == 3
    assert "Blobs made" in full_prompt(st.gs.ws.cheap.calls[0])


async def test_thread_parts_translated_per_part(st: CardsStand) -> None:
    parts = [["One part.", "Two part.", "Three part."]] * 3
    st.gs.ws.cheap.push(*[ru_reply("Раз.", "Два.", "Три.")] * 3)

    await st.generate(*EN, fmt="thread", parts=parts, lang="en")

    [card] = st.cards()
    assert "1/3 · 9/280" in card.text and "One part." in card.text
    assert "1/3 Раз." in card.text and "3/3 Три." in card.text


async def test_cheap_down_card_still_shown_with_note(st: CardsStand) -> None:
    st.gs.ws.cheap.push(*[PermanentProviderError("deepseek", status=400)] * 8)

    await st.generate(*EN, lang="en")

    [card] = st.cards()
    assert "перевод недоступен" in card.text
    assert EN[0] in card.text and "blockquote" not in card.text


async def test_ru_account_gets_no_translation(st: CardsStand) -> None:
    await st.generate()

    assert st.gs.ws.cheap.calls == []
    [card] = st.cards()
    assert "blockquote" not in card.text and "перевод" not in card.text


async def test_translation_stored_with_draft_revision(st: CardsStand) -> None:
    st.gs.ws.cheap.push(*[ru_reply(f"Перевод {i}") for i in (1, 2, 3)])
    run = await st.generate(*EN, lang="en")

    d1 = (await st.finals(run))[0]
    rows = await st.rows("SELECT lint_report -> 'translation' FROM drafts WHERE id = :d", d=d1)
    assert rows[0][0]["ru"] == ["Перевод 1"]


async def test_edit_refreshes_translation(st: CardsStand) -> None:
    st.gs.ws.cheap.push(*[ru_reply(f"Перевод {i}") for i in (1, 2, 3)])
    run = await st.generate(*EN, lang="en")
    d1: UUID = (await st.finals(run))[0]
    st.gs.ws.cheap.push(ru_reply("Новый перевод правки"))

    await st.owner.press("✏️ Править")
    await st.owner.say("Blobs cut rollup fees by a third, visible in my transactions all week")

    rows = await st.rows("SELECT lint_report -> 'translation' FROM drafts WHERE id = :d", d=d1)
    assert rows[0][0]["ru"] == ["Новый перевод правки"]
    assert "Новый перевод правки" in "\n".join(m.text for m in st.tg.sent[-3:])
    assert "Blobs cut rollup" in full_prompt(st.gs.ws.cheap.calls[-1])
