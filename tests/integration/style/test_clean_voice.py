"""«Чистый голос»: паспорт собирается только из своих постов без AI-шаблонов (feat/clean-voice).

Пост с найденным шаблоном (детектор `ai_slop`, config/ai_slop.toml) не идёт ни в выборку
модели, ни в примеры паспорта, но остаётся своим (stance = own) — для цифр и приёмов.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.clean_voice_stand import CLEAN_POSTS, SLOP_POSTS, seed_own, source_ids_of
from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.style_stand import make_services, model_reply, new_account, seed_chunks
from xcopilot.db.uow import UnitOfWork
from xcopilot.style.clean_voice import voice_filter

SF = async_sessionmaker[AsyncSession]


async def test_voice_filter_splits_clean_and_slop(session_factory: SF) -> None:
    acc = await new_account(session_factory)
    clean = await seed_own(session_factory, acc, CLEAN_POSTS)
    slop = await seed_own(session_factory, acc, SLOP_POSTS)
    await seed_chunks(session_factory, acc, ["чужой — пост"], kind="competitor_post")
    async with UnitOfWork(session_factory) as uow:
        report = await voice_filter(uow.session, acc)
    assert report.clean_ids == set(clean)
    assert report.dirty_ids == set(slop)
    codes = {doc.source_id: {h.code for h in hits} for doc, hits in report.dirty}
    assert "em_dash" in codes[slop[0]]
    assert "binary_contrast" in codes[slop[1]]
    assert "banned_word" in codes[slop[2]]


async def test_slop_posts_never_reach_passport_model_or_examples(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await new_account(session_factory)
    clean_chunks = await seed_chunks(session_factory, acc, CLEAN_POSTS)
    slop_chunks = await seed_chunks(session_factory, acc, SLOP_POSTS)
    writer = FakeLLM(name="codex_cli").push(
        model_reply(
            example_chunk_ids=[str(slop_chunks[0]), str(clean_chunks[0])],
            signature_patterns=[{"text": "контраст", "evidence": [str(slop_chunks[1])]}],
        )
    )
    _, extractor = make_services(session_factory, clock, writer)
    passport = await extractor.extract(acc)
    sent = " ".join(m.content for m in writer.calls[0].request.messages)
    for post in SLOP_POSTS:
        assert post not in sent
    assert CLEAN_POSTS[0] in sent
    assert [e.chunk_id for e in passport.examples] == [str(clean_chunks[0])]
    assert passport.signature_patterns == []  # доказательство — только грязный пост
    clean_sources = await source_ids_of(session_factory, clean_chunks)
    assert set(passport.built_from) == {str(s) for s in clean_sources}


async def test_slop_post_stays_own(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    slop = await seed_own(session_factory, acc, SLOP_POSTS[:1])
    await seed_own(session_factory, acc, CLEAN_POSTS)
    writer = FakeLLM(name="codex_cli").push(model_reply())
    _, extractor = make_services(session_factory, clock, writer)
    await extractor.extract(acc)
    async with session_factory() as s:
        stance = (
            await s.execute(text("SELECT stance FROM sources WHERE id = :i"), {"i": slop[0]})
        ).scalar_one()
    assert stance == "own"


async def test_rough_counts_only_clean_posts(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    await seed_own(session_factory, acc, CLEAN_POSTS[:3] + SLOP_POSTS)
    writer = FakeLLM(name="codex_cli").push(model_reply())
    _, extractor = make_services(session_factory, clock, writer)
    assert (await extractor.extract(acc)).rough is True  # 3 чистых < 5


async def test_model_ids_with_chunk_prefix_are_kept(session_factory: SF, clock: FakeClock) -> None:
    """Промпт просит id «из label (`chunk_<id>`)»: модель отвечает с префиксом — не терять.

    Живая пересборка 19.11 (v7) отбросила все слова, приёмы и примеры модели из-за префикса.
    """
    acc = await new_account(session_factory)
    ids = await seed_chunks(session_factory, acc, CLEAN_POSTS)
    writer = FakeLLM(name="codex_cli").push(
        model_reply(
            lexicon_use=[{"text": "rn", "evidence": [f"chunk_{ids[2]}"]}],
            signature_patterns=[{"text": "цифры коротко", "evidence": [f"chunk_{ids[1]}", "x"]}],
            example_chunk_ids=[f"chunk_{ids[0]}", str(ids[3])],
        )
    )
    _, extractor = make_services(session_factory, clock, writer)
    passport = await extractor.extract(acc)
    assert passport.lexicon.use == ["rn"]
    assert passport.signature_patterns == ["цифры коротко"]
    assert [e.chunk_id for e in passport.examples] == [str(ids[0]), str(ids[3])]
    assert {c for e in passport.evidence for c in e.chunk_ids} == {str(ids[1]), str(ids[2])}
