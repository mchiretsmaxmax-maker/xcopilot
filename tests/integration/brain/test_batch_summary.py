"""Одна сводка на пачку вместо карточки на каждую ссылку (feat/auto-outcome, ADR 0033)."""

from __future__ import annotations

from uuid import UUID, uuid4

from tests.brain_stand import BrainStand, analysis_json
from tests.ingest_stand import buttons
from xcopilot.brain import batch as brain_batch
from xcopilot.brain import signals
from xcopilot.brain.signals import Signal

HIT = Signal(
    origin="public_metrics", views=10000, likes=400, replies=20, reposts=10, post_age_hours=72
)
NORMAL = Signal(origin="public_metrics", views=10000, likes=100, replies=5, post_age_hours=72)
FLOP = Signal(origin="public_metrics", views=10000, likes=20, post_age_hours=72)


def body(i: int) -> str:
    return f"i lost ${i}k in 3 weeks\nthen i stopped number {i}\nnow i sleep fine {i}"


async def _batch(brain: BrainStand, sigs: list[Signal | None]) -> tuple[str, list[UUID]]:
    batch = str(uuid4())
    ids = []
    for i, sig in enumerate(sigs):
        sid = await brain.material(
            body(i), meta={"batch": batch, "accepted_by": str(brain.user_id)}
        )
        if sig is not None:
            async with brain.uow() as uow:
                await signals.add_signal(uow.session, brain.account_id, sid, sig)
        ids.append(sid)
    return batch, ids


async def _analyze(brain: BrainStand, sids: list[UUID]) -> None:
    for sid in sids:
        rows = await brain.rows("SELECT text FROM documents WHERE source_id = :s", s=sid)
        brain.llm.push(analysis_json(rows[0].text))
        assert await brain.analyzer.analyze(brain.account_id, sid) is not None


async def test_batch_gets_one_summary_with_counts(brain: BrainStand) -> None:
    batch, ids = await _batch(brain, [HIT, HIT, NORMAL, FLOP, None])
    await _analyze(brain, ids)
    sent = brain.notifier.sent
    assert len(sent) == 1, [t for _, t, _ in sent]
    _, text, keyboard = sent[0]
    lines = text.splitlines()
    assert lines[0] == "📥 Разобрал 5: 🔥 2 залетели · 👍 1 норм · 👎 1 не зашли · ❔ 1 без цифр"
    assert lines[1].startswith("Чаще всего хуки: ") and "(5)" in lines[1]
    assert lines[2].startswith("Приёмы: ") and "(5)" in lines[2]
    assert "🧩 Разбор" not in text
    got = buttons(keyboard)
    assert ("🧩 Показать разборы", f"mbs:{batch}:show:0") in got
    assert ("⭐ Всё это моё", f"mbs:{batch}:own") in got
    assert ("💡 Всё вдохновение", f"mbs:{batch}:inspiration") in got
    assert ("❔ Оценить без цифр (1)", f"mbs:{batch}:rate") in got


async def test_all_rated_batch_has_no_rate_button(brain: BrainStand) -> None:
    _, ids = await _batch(brain, [HIT, FLOP])
    await _analyze(brain, ids)
    [(_, text, keyboard)] = brain.notifier.sent
    assert text.startswith(
        "📥 Разобрал 2: 🔥 1 залетели · 👍 0 норм · 👎 1 не зашли · ❔ 0 без цифр"
    )
    assert not any(data.endswith(":rate") for _, data in buttons(keyboard))


async def test_timeout_summary_says_still_analyzing(brain: BrainStand) -> None:
    batch, ids = await _batch(brain, [HIT, NORMAL, FLOP])
    await _analyze(brain, ids[:2])
    assert brain.notifier.sent == []
    jobs = await brain.rows(
        "SELECT payload, run_at FROM jobs WHERE kind = :k", k=brain_batch.SUMMARY_KIND
    )
    assert [j.payload for j in jobs] == [{"batch": batch}]
    assert jobs[0].run_at > brain.clock.now()

    assert await brain_batch.send_summary(brain.uow, brain.notifier, brain.account_id, batch)
    [(_, text, _)] = brain.notifier.sent
    assert text.startswith(
        "📥 Разобрал 2: 🔥 1 залетели · 👍 1 норм · 👎 0 не зашли · ❔ 0 без цифр"
    )
    assert "ещё разбираю 1" in text
    # сводка уже ушла: повтор по таймауту молчит, опоздавший материал — своей карточкой
    assert not await brain_batch.send_summary(brain.uow, brain.notifier, brain.account_id, batch)
    await _analyze(brain, ids[2:])
    assert len(brain.notifier.sent) == 2
    assert brain.notifier.sent[1][1].startswith("🧩 Разбор")


async def test_single_material_with_auto_outcome_skips_question(brain: BrainStand) -> None:
    sid = await brain.material(body(1))
    async with brain.uow() as uow:
        await signals.add_signal(
            uow.session,
            brain.account_id,
            sid,
            Signal(origin="public_metrics", views=30000, likes=1200, replies=40, post_age_hours=72),
        )
    await _analyze(brain, [sid])
    [(_, text, keyboard)] = brain.notifier.sent
    assert text.startswith("🧩 Разбор")
    assert "Зашёл: 🔥 (1.2k лайков, по цифрам)" in text
    assert "Как он зашёл?" not in text
    got = buttons(keyboard)
    assert ("✏️ Поправить оценку", f"moe:{sid}") in got
    assert not any(data.startswith("mo:") for _, data in got)


async def test_single_material_without_numbers_still_asks(brain: BrainStand) -> None:
    sid = await brain.material(body(2))
    await _analyze(brain, [sid])
    [(_, text, _)] = brain.notifier.sent
    assert "Как он зашёл?" in text
