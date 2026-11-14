"""Выборка примеров и извлечение паспорта моделью `writer` (ТЗ этапа 8, задачи 3–4, 8)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.style_stand import LOWER_POSTS, make_services, model_reply, new_account, seed_chunks
from xcopilot.ingest.chunker import count_tokens
from xcopilot.style.errors import NoOwnPosts
from xcopilot.style.passport import StylePassport

SF = async_sessionmaker[AsyncSession]


def own_post(i: int) -> str:
    return f"пост {i} про сети " + "слово " * (3 + i % 4)


async def test_extractor_output_validates_schema(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    ids = await seed_chunks(session_factory, acc, LOWER_POSTS)
    writer = FakeLLM(name="anthropic").push(
        model_reply(
            lexicon_use=[{"text": "блобы", "evidence": [str(ids[0])]}],
            lexicon_avoid=[{"text": "революционный", "evidence": [str(ids[1])]}],
            formats=[{"format": "short", "share": 1.0}],
            signature_patterns=[{"text": "факт без вступления", "evidence": [str(ids[2])]}],
            example_chunk_ids=[str(ids[0]), str(ids[3])],
            anti_examples=["Давайте погрузимся!"],
        )
    )
    _, extractor = make_services(session_factory, clock, writer)
    passport = await extractor.extract(acc)
    assert StylePassport.model_validate(passport.model_dump()) == passport
    assert passport.summary and passport.tone
    assert passport.lexicon.use == ["блобы"]
    assert passport.lexicon.avoid == ["революционный"]
    assert passport.signature_patterns == ["факт без вступления"]
    assert [e.chunk_id for e in passport.examples] == [str(ids[0]), str(ids[3])]
    assert passport.examples[0].text == LOWER_POSTS[0]
    assert passport.anti_examples == ["Давайте погрузимся!"]
    assert {c for e in passport.evidence for c in e.chunk_ids} >= {str(ids[0]), str(ids[2])}
    assert passport.rough is False
    assert len(writer.calls) == 1


async def test_rule_without_evidence_is_dropped(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    ids = await seed_chunks(session_factory, acc, LOWER_POSTS)
    other = await new_account(session_factory, "other_acc")
    foreign = (await seed_chunks(session_factory, other, ["чужой пост про сети"]))[0]
    writer = FakeLLM(name="anthropic").push(
        model_reply(
            banned_phrases=[
                {"text": "погрузимся", "evidence": []},
                {"text": "в мире блокчейна", "evidence": ["не-uuid"]},
                {"text": "чужое", "evidence": [str(foreign)]},
                {"text": "delve", "evidence": [str(ids[0])]},
            ],
            lexicon_use=[{"text": "без доказательств"}],
            signature_patterns=[{"text": "тоже без", "evidence": []}],
        )
    )
    _, extractor = make_services(session_factory, clock, writer)
    passport = await extractor.extract(acc)
    assert passport.hard_rules.banned_phrases == ["delve"]
    assert passport.lexicon.use == []
    assert passport.signature_patterns == []
    claims = {e.claim for e in passport.evidence}
    assert "delve" in claims
    assert "погрузимся" not in claims


async def test_examples_reference_only_own_voice_chunks(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await new_account(session_factory)
    own = await seed_chunks(session_factory, acc, LOWER_POSTS)
    rival = await seed_chunks(
        session_factory, acc, ["пост конкурента про сети"], kind="competitor_post"
    )
    note = await seed_chunks(session_factory, acc, ["заметка с фактами про сети"], kind="note")
    writer = FakeLLM(name="anthropic").push(
        model_reply(example_chunk_ids=[str(rival[0]), str(own[0]), str(note[0])])
    )
    _, extractor = make_services(session_factory, clock, writer)
    passport = await extractor.extract(acc)
    assert [e.chunk_id for e in passport.examples] == [str(own[0])]
    sent = " ".join(m.content for m in writer.calls[0].request.messages)
    assert "пост конкурента про сети" not in sent
    assert "заметка с фактами про сети" not in sent
    assert LOWER_POSTS[0] in sent


async def test_sampling_stays_within_token_budget_and_covers_formats(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await new_account(session_factory)
    await seed_chunks(session_factory, acc, [own_post(i) for i in range(30)], fmt="short")
    await seed_chunks(session_factory, acc, [f"тред {own_post(i)}" for i in range(4)], fmt="thread")
    await seed_chunks(session_factory, acc, [f"лонг {own_post(i)}" for i in range(4)], fmt="long")
    _, extractor = make_services(session_factory, clock, FakeLLM(name="anthropic"))
    budget = 80
    samples = await extractor.sample_examples(acc, budget)
    assert samples
    assert sum(s.tokens for s in samples) <= budget
    assert all(s.tokens == count_tokens(s.text) for s in samples)
    assert {s.format for s in samples} == {"short", "thread", "long"}
    assert await extractor.sample_examples(acc, 0) == []
    everything = await extractor.sample_examples(acc, 100_000)
    assert len(everything) == 38


async def test_less_than_5_samples_marks_rough(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    await seed_chunks(session_factory, acc, LOWER_POSTS[:3])
    writer = FakeLLM(name="anthropic").push(model_reply(), model_reply())
    _, extractor = make_services(session_factory, clock, writer)
    assert (await extractor.extract(acc)).rough is True
    await seed_chunks(session_factory, acc, LOWER_POSTS[3:])
    assert (await extractor.extract(acc)).rough is False


async def test_extract_without_own_posts_fails(session_factory: SF, clock: FakeClock) -> None:
    acc = await new_account(session_factory)
    await seed_chunks(session_factory, acc, ["чужое"], kind="competitor_post")
    _, extractor = make_services(session_factory, clock, FakeLLM(name="anthropic"))
    with pytest.raises(NoOwnPosts):
        await extractor.extract(acc)


async def test_hard_rules_from_stats_override_model_output(
    session_factory: SF, clock: FakeClock
) -> None:
    acc = await new_account(session_factory)
    await seed_chunks(session_factory, acc, LOWER_POSTS)
    writer = FakeLLM(name="anthropic").push(
        model_reply(
            hard_rules={
                "casing": "any",
                "emoji": "free",
                "hashtags": "free",
                "links": "free",
                "max_len": 5000,
                "banned_phrases": ["навязанное"],
            }
        )
    )
    _, extractor = make_services(session_factory, clock, writer)
    rules = (await extractor.extract(acc)).hard_rules
    assert rules.casing == "lower"
    assert rules.emoji == "none"
    assert rules.hashtags == "none"
    assert rules.max_len == 280
    assert rules.banned_phrases == []


async def test_passport_samples_only_own_stance(session_factory: SF, clock: FakeClock) -> None:
    """Для паспорта берутся только свои материалы; чужие виды и непроиндексированные — нет."""
    acc = await new_account(session_factory)
    own = await seed_chunks(session_factory, acc, ["мой пост про сети"])
    await seed_chunks(session_factory, acc, ["понравившийся чужой пост"], kind="liked_sample")
    await seed_chunks(session_factory, acc, ["пост конкурента"], kind="competitor_post")
    await seed_chunks(session_factory, acc, ["заметка"], kind="note")
    await seed_chunks(session_factory, acc, ["мой, но не готов"], status="parsed")
    _, extractor = make_services(session_factory, clock, FakeLLM(name="anthropic"))
    samples = await extractor.sample_examples(acc, 10_000)
    assert [s.id for s in samples] == own
