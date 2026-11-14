"""События и стоимость разбора (этап 7A, задача 10; общий критерий готовности)."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.brain_stand import BrainStand, analysis_json
from xcopilot.llm.pricing import Usage

TEXT = "i lost $40k in 3 weeks\nthen i stopped\nnow i sleep fine"


async def test_analysis_emits_events_and_cost(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    brain.llm.usage = Usage(input_tokens=500, cache_read_tokens=1500, output_tokens=550)
    brain.llm.push(analysis_json(TEXT))
    await brain.analyzer.analyze(brain.account_id, sid)

    analyzed = await brain.rows(
        "SELECT props, entity_id FROM events WHERE account_id = :a AND type = 'material.analyzed'",
        a=brain.account_id,
    )
    assert len(analyzed) == 1
    props = analyzed[0].props
    assert props["source_id"] == str(sid)
    assert props["analyzer_version"] == "brain.analyze_material.v1"
    assert props["format"] == "post"

    calls = await brain.rows(
        "SELECT id, purpose, cost_micro_usd, entity_type, entity_id FROM llm_calls"
        " WHERE account_id = :a",
        a=brain.account_id,
    )
    assert len(calls) == 1
    call = calls[0]
    assert call.purpose == "brain.analyze"
    assert (call.entity_type, call.entity_id) == ("source", sid)
    assert call.cost_micro_usd > 0
    row = (
        await brain.rows(
            "SELECT llm_call_id, model, analyzer_version, taxonomy_version FROM material_analyses"
            " WHERE source_id = :s AND is_current",
            s=sid,
        )
    )[0]
    assert row.llm_call_id == call.id
    assert row.model == "deepseek-flash"
    assert (row.analyzer_version, row.taxonomy_version) == ("brain.analyze_material.v1", 1)

    status = await brain.rows("SELECT analysis_status FROM sources WHERE id = :s", s=sid)
    assert status[0].analysis_status == "analyzed"
    # чужой материал без сигнала получает отбор по умолчанию, владельцу уходит карточка
    origins = await brain.rows("SELECT origin FROM material_signals WHERE source_id = :s", s=sid)
    assert [o.origin for o in origins] == ["owner_pick"]
    assert brain.notifier.sent and brain.notifier.sent[-1][1].startswith("🧩 Разбор")
    picked = await brain.rows(
        "SELECT count(*) AS n FROM events WHERE account_id = :a AND type = 'material.signal_added'",
        a=brain.account_id,
    )
    assert picked[0].n == 1


async def test_hundred_materials_ten_minutes_under_15_cents(brain: BrainStand) -> None:
    """Критерий готовности: 100 материалов за 10 минут и дешевле $0.15 (цены пика DeepSeek)."""
    brain.clock._now = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 03:00 UTC — пик DeepSeek
    # 2 000 входных без скидки кэша (честно дороже раздела 3.4) и 550 на выходе
    brain.llm.usage = Usage(input_tokens=2000, output_tokens=550)
    brain.llm.latency = 4.0  # 4 с на ответ модели
    ids = []
    for i in range(100):
        body = f"{i} things i learned\nline two {i}\nlast line {i}"
        ids.append(await brain.material(body, author_handle=f"a{i % 40}"))
        brain.llm.push(analysis_json(body))
    start = brain.clock.now()
    for sid in ids:
        await brain.analyzer.analyze(brain.account_id, sid)
    elapsed = (brain.clock.now() - start).total_seconds()
    assert elapsed <= 600
    total = (
        await brain.rows(
            "SELECT coalesce(sum(cost_micro_usd), 0) AS c, count(*) AS n FROM llm_calls"
            " WHERE account_id = :a AND purpose LIKE 'brain.%'",
            a=brain.account_id,
        )
    )[0]
    assert total.n == 100
    assert 0 < total.c < 150_000
    assert (
        await brain.count("material_analyses", "account_id = :a AND is_current", a=brain.account_id)
        == 100
    )
