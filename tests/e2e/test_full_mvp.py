"""Этап 15, задача 1: весь MVP одним прогоном на собранном приложении (build_app).

Настоящие: БД, бот (диспетчер), воркер, очередь, сервисы всех этапов. Подделки: модели
(шлюз по ключу промпта), X, Telegram, эмбеддер. Ни одна задача не должна умереть.
"""

from __future__ import annotations

from datetime import timedelta

from tests.e2e.mvp_stand import EDITED, MATERIALS, SAMPLES, MvpStand


async def test_e2e_full_mvp_loop(mvp: MvpStand) -> None:
    owner = mvp.owner

    # онбординг: ниша, язык, примеры стиля, подтверждение
    await mvp.onboard(SAMPLES)
    acc = mvp.account_id
    assert await mvp.count("account_members", "account_id = :a AND role = 'owner'", a=acc) == 1

    # ингест: примеры владельца и материалы разобраны на куски с векторами
    for body in MATERIALS:
        await mvp.send_material(body)
    await mvp.drain()
    assert await mvp.count("sources", "account_id = :a AND stance = 'own'", a=acc) >= len(SAMPLES)
    assert await mvp.count("sources", "account_id = :a AND status = 'indexed'", a=acc) >= len(
        SAMPLES
    ) + len(MATERIALS)
    assert await mvp.count("embeddings", "account_id = :a", a=acc) > 0

    # паспорт стиля собран и включён
    await mvp.build_passport()
    assert await mvp.count("style_passports", "account_id = :a AND status = 'active'", a=acc) == 1

    # темы → черновики → ворота → карточка
    await mvp.request_topics()
    assert await mvp.count("topics", "account_id = :a", a=acc) >= 3
    await mvp.write_first_topic()
    [card] = mvp.cards()
    assert card.text.startswith("📝 Вариант 1/3 · пост")
    assert await mvp.count("drafts", "account_id = :a", a=acc) >= 3
    gated = await mvp.count(
        "drafts", "account_id = :a AND lint_report ->> 'gate' IS NOT NULL", a=acc
    )
    assert gated >= 3

    # правка → публикация по ссылке
    await owner.press("✏️ Править")
    await owner.say(EDITED)
    await owner.press("✔️ Опубликовал")
    mvp.own_post("1900000000000000042", EDITED, likes=40, views=2000)
    await owner.say("https://x.com/me/status/1900000000000000042")
    pubs = await mvp.rows("SELECT matched_by, final_text FROM publications WHERE account_id = :a",
                          a=acc)  # fmt: skip
    assert [(p[0], p[1]) for p in pubs] == [("user_link", EDITED)]
    assert await mvp.count("reviews", "account_id = :a AND action = 'edit'", a=acc) == 1

    # синк своих метрик: свой пост найден, снимок на отметке 24 часа
    mvp.clock.advance(26 * 3600)
    await mvp.run_job("analytics.sync")
    await mvp.drain()
    assert await mvp.count("post_metric_snapshots", "account_id = :a", a=acc) >= 1

    # предложение знания из материалов
    await mvp.run_job("brain.extract")
    proposed = await mvp.count("knowledge_items", "account_id = :a AND origin = 'materials'", a=acc)
    assert proposed >= 1

    # ежедневный отчёт владельцу
    before = len(mvp.telegram.sent)
    mvp.clock.advance(timedelta(hours=1).total_seconds())
    await mvp.run_job("reports.daily")
    report = [m.text for m in mvp.telegram.sent[before:] if m.chat_id == owner.user_id]
    assert any(t.startswith("Вчера:") for t in report), report

    # весь прогон — без мёртвых задач и без записи в X
    dead = await mvp.rows("SELECT kind, last_error FROM jobs WHERE status = 'dead'")
    assert dead == []
    assert {c.split(":")[0] for c in mvp.x.calls} <= {
        "get_post", "get_own_posts", "iter_own_history", "get_user",
    }  # fmt: skip
