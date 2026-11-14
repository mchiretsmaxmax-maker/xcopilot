"""Словари разбора (этап 7A, задача 2)."""

from __future__ import annotations

from tests.brain_stand import BrainStand, analysis_json
from xcopilot.brain import taxonomy
from xcopilot.brain.schemas import ProposedTerm

TEXT = "i lost $40k in 3 weeks\nthen i stopped\nnow i sleep fine"


async def test_taxonomy_seed_loaded_once_and_versioned(brain: BrainStand) -> None:
    global_terms = "account_id = (SELECT id FROM accounts WHERE kind = 'global')"
    before = await brain.count("taxonomy_terms", global_terms)
    async with brain.uow() as uow:
        first = await taxonomy.load_seed(uow.session)
    async with brain.uow() as uow:
        second = await taxonomy.load_seed(uow.session)
        version = await taxonomy.current_version(uow.session, brain.account_id)
        hooks = await taxonomy.active_terms(uow.session, brain.account_id, "hook_type")
    assert first == 0 and second == 0  # засеяно миграцией 0005: повторной вставки нет
    assert await brain.count("taxonomy_terms", global_terms) == before
    assert version == 1
    assert len(hooks) == 14
    assert all(t.description for t in hooks)  # определения дописаны из config/taxonomy.toml
    assert {t.key for t in hooks} >= {"number_personal", "question", "plain_statement"}


async def test_unknown_term_replaced_by_nearest_and_proposed(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    brain.llm.push(analysis_json(TEXT, hook_type="number_personnal", devices=["specific_numbr"]))
    result = await brain.analyzer.analyze(brain.account_id, sid)
    assert result is not None
    assert result.hook_type == "number_personal"
    assert result.devices == ["specific_number"]
    row = (
        await brain.rows(
            "SELECT hook_type, devices FROM material_analyses WHERE source_id = :s AND is_current",
            s=sid,
        )
    )[0]
    assert row.hook_type == "number_personal"
    assert row.devices == ["specific_number"]
    proposed = await brain.rows(
        "SELECT facet, key, status, created_by FROM taxonomy_terms"
        " WHERE account_id = :a ORDER BY key",
        a=brain.account_id,
    )
    assert [(p.facet, p.key, p.status, p.created_by) for p in proposed] == [
        ("hook_type", "number_personnal", "proposed", "model"),
        ("device", "specific_numbr", "proposed", "model"),
    ]


async def test_proposed_term_unused_until_accepted(brain: BrainStand) -> None:
    term = ProposedTerm(
        facet="hook_type",
        key="loss_confession",
        title="признание потери",
        description="первая строка признаётся в потере денег с конкретной суммой",
        quote="i lost $40k",
    )
    async with brain.uow() as uow:
        created = await taxonomy.propose_term(uow.session, brain.account_id, term)
    assert created.status == "proposed"
    async with brain.uow() as uow:
        keys = {
            t.key for t in await taxonomy.active_terms(uow.session, brain.account_id, "hook_type")
        }
    assert "loss_confession" not in keys

    sid = await brain.material(TEXT)
    brain.llm.push(analysis_json(TEXT, hook_type="loss_confession"))
    result = await brain.analyzer.analyze(brain.account_id, sid)
    assert result is not None
    assert result.hook_type != "loss_confession"  # не действует до подтверждения
    prompt = brain.llm.calls[-1].request.messages[0].content
    assert "loss_confession" not in prompt

    async with brain.uow() as uow:
        await taxonomy.accept_term(uow.session, brain.account_id, created.id, brain.user_id)
    async with brain.uow() as uow:
        keys = {
            t.key for t in await taxonomy.active_terms(uow.session, brain.account_id, "hook_type")
        }
    assert "loss_confession" in keys


async def test_accepting_term_bumps_taxonomy_version(brain: BrainStand) -> None:
    term = ProposedTerm(
        facet="device",
        key="price_anchor",
        title="якорь цены",
        description="сравнение с заметной суммой, чтобы цифра выглядела крупнее",
        quote="i lost $40k",
    )
    async with brain.uow() as uow:
        assert await taxonomy.current_version(uow.session, brain.account_id) == 1
        created = await taxonomy.propose_term(uow.session, brain.account_id, term)
    async with brain.uow() as uow:
        assert await taxonomy.current_version(uow.session, brain.account_id) == 1
        new_version = await taxonomy.accept_term(
            uow.session, brain.account_id, created.id, brain.user_id
        )
    async with brain.uow() as uow:
        assert await taxonomy.current_version(uow.session, brain.account_id) == 2
    assert new_version == 2
    row = (
        await brain.rows(
            "SELECT status, since_version FROM taxonomy_terms WHERE id = :i", i=created.id
        )
    )[0]
    assert (row.status, row.since_version) == ("active", 2)
    other = await make_other_account_version(brain)
    assert other == 1  # версия аккаунта не трогает чужие аккаунты


async def make_other_account_version(brain: BrainStand) -> int:
    from tests.brain_stand import make_brain_stand

    other = await make_brain_stand(brain.sf, brain.clock)
    async with other.uow() as uow:
        return await taxonomy.current_version(uow.session, other.account_id)
