"""Этап 15 (v3.1), задача 11: петля обучения на собранном приложении.

материалы → разбор → знание → вариант 🧪 → одобрение → публикация → исход → вес вырос;
и защита: плохая пачка материалов → откат базы из бота → знание больше не идёт в черновики.
"""

from __future__ import annotations

from datetime import timedelta

from tests.e2e.mvp_stand import HOOK, MATERIALS, SAMPLES, SPAM, MvpStand


async def _ready(mvp: MvpStand) -> None:
    await mvp.onboard(SAMPLES)
    await mvp.drain()
    await mvp.build_passport()


async def test_e2e_learning_loop(mvp: MvpStand) -> None:
    await _ready(mvp)
    for i, body in enumerate(MATERIALS):
        await mvp.send_material(body, author=f"author{i}")
    await mvp.drain()
    analysed = await mvp.count(
        "material_analyses", "account_id = :a AND is_current", a=mvp.account_id
    )
    assert analysed >= len(MATERIALS)

    item = await mvp.extract_knowledge(HOOK)
    await mvp.run_nightly()
    assert await mvp.item_status(item) == "probation"

    await mvp.write_topic("почему комиссии в l2 падают")
    probation = await mvp.probation_draft(item)
    card = await mvp.show_variant(probation)
    assert "🧪" in card.text

    await mvp.press_on(card, "✅ Одобрить")
    await mvp.owner.press("✔️ Опубликовал")
    body = await mvp.draft_text(probation)
    mvp.own_post("1900000000000000077", body, likes=60, views=1500)
    await mvp.owner.say("https://x.com/me/status/1900000000000000077")
    assert await mvp.count("publications", "draft_id = :d", d=probation) == 1

    before = await mvp.item_weights(item)
    mvp.clock.advance(26 * 3600)
    await mvp.run_job("analytics.sync")
    await mvp.drain()
    after = await mvp.item_weights(item)
    assert after["eff_alpha"] > before["eff_alpha"]  # свой пост подтвердил приём
    assert after["quality"] > before["quality"]
    assert await mvp.count("jobs", "status = 'dead'") == 0


async def test_e2e_bad_batch_then_rollback(mvp: MvpStand) -> None:
    await _ready(mvp)
    good_item = await mvp.extract_knowledge(HOOK, materials=MATERIALS)
    await mvp.run_nightly()
    await mvp.confirm_item(good_item)  # владелец подтвердил: на испытании никого не осталось
    assert await mvp.item_status(good_item) == "active"
    mvp.clock.advance(timedelta(days=1).total_seconds())

    # плохая пачка: спам одного «автора» даёт вредное знание
    bad_item = await mvp.extract_knowledge(SPAM, materials=[f"{SPAM} #{i}" for i in range(6)])
    await mvp.run_nightly()
    assert await mvp.item_status(bad_item) == "probation"
    assert bad_item in await mvp.pack_items("комиссии в l2")  # единственное на испытании

    # откат базы из бота на день до пачки
    await mvp.rollback_from_bot(days_back=1)
    assert await mvp.item_status(bad_item) == "archived"
    assert await mvp.item_status(good_item) == "active"  # до пачки — нетронуто
    assert bad_item not in await mvp.pack_items("комиссии в l2")
    assert (
        await mvp.count(
            "events", "account_id = :a AND type = 'knowledge.rollback'", a=mvp.account_id
        )
        == 1
    )
    assert await mvp.count("jobs", "status = 'dead'") == 0
