"""Этап 13: обучение на правках (БД, подделка writer; живых вызовов нет)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import NOW
from tests.integration.db.brain_helpers import draft
from tests.learning_stand import (
    Learn,
    humanness_candidate,
    lexicon_candidate,
    make_learn,
    result,
)
from xcopilot.brain import lifecycle, owner, segments
from xcopilot.brain.schemas import KnowledgeCandidate
from xcopilot.db.repos.passports import PassportsRepo
from xcopilot.learning.effect import WINDOW, item_effect
from xcopilot.learning.from_reviews import PROMPT, detect_removed_devices, propose
from xcopilot.learning.passport_sync import apply_confirmed_voice_item
from xcopilot.learning.triggers import should_run
from xcopilot.metrics.events import EventType, emit
from xcopilot.quality.knowledge import DbClassCRules
from xcopilot.quality.lint.engine import LintContext, LintDraft, Linter
from xcopilot.style.passport import StylePassport
from xcopilot.style.service import _rules_column

BEFORE2 = "Let's break down why gas is cheap now"
AFTER2 = "Why gas is cheap now"


async def two_edits(L: Learn) -> None:
    await L.review()
    await L.review(before=BEFORE2, after=AFTER2)


async def proposed(L: Learn, *cands: KnowledgeCandidate) -> list[KnowledgeCandidate]:
    L.llm.push(result(*cands))
    return await propose(L.ctx)


# ---------------------------------------------------------------- кандидаты


async def test_single_edit_does_not_make_item(session: AsyncSession) -> None:
    L = await make_learn(session)
    await L.review()
    out = await proposed(L, lexicon_candidate(supporting=["M1"]))
    assert out == []
    assert await L.edit_items() == []


async def test_edit_candidates_are_voice_or_humanness_only(session: AsyncSession) -> None:
    L = await make_learn(session)
    await two_edits(L)
    craft = lexicon_candidate(
        kind="thesis",
        layer="craft",
        target_metric="engagement",
        statement="Личная цифра в первой строке даёт больше ответов",
        guidance="Начни с личной цифры из своего опыта",
    )
    out = await proposed(L, craft, lexicon_candidate())
    assert [c.layer for c in out] == ["voice"]
    items = await L.edit_items()
    assert [i["layer"] for i in items] == ["voice"]
    assert L.llm.gen_calls[0].prompt_key == PROMPT


async def test_voice_candidate_waits_for_owner(session: AsyncSession) -> None:
    L = await make_learn(session)
    await two_edits(L)
    await proposed(L, lexicon_candidate())
    (item,) = await L.edit_items()
    assert (item["status"], item["owner_state"], item["risk_class"]) == ("candidate", "none", "A")
    await lifecycle.run(L.ctx)
    assert (await L.edit_items())[0]["status"] == "candidate"
    pending = await segments.class_a_pending(session, L.acc)
    assert [p.id for p in pending] == [item["id"]]
    await owner.confirm(L.ctx, item["id"], L.uid)
    assert (await L.edit_items())[0]["status"] == "active"


async def test_candidate_needs_two_distinct_drafts_and_real_phrase(session: AsyncSession) -> None:
    L = await make_learn(session)
    d = await draft(session, L.acc)
    await L.review(draft_id=d)
    await L.review(draft_id=d, before=BEFORE2, after=AFTER2)  # два шага одного черновика
    assert await proposed(L, lexicon_candidate()) == []
    # фраза в кавычках, которой в правках нет, — отбрасывается кодом
    L2 = await make_learn(session)
    await two_edits(L2)
    fake = lexicon_candidate(statement="Ты убираешь «gm frens» в начале поста")
    assert await proposed(L2, fake) == []


# ---------------------------------------------------------------- паспорт


async def passport_v1(L: Learn) -> None:
    body = StylePassport(summary="Короткий голос")
    await PassportsRepo(L.session, L.acc).add(
        version=1,
        status="active",
        body=body.model_dump(mode="json"),
        hard_rules=_rules_column(body.hard_rules),
        created_by="user",
    )


async def test_confirmed_banned_phrase_creates_passport_version(session: AsyncSession) -> None:
    L = await make_learn(session)
    await passport_v1(L)
    await two_edits(L)
    await proposed(L, lexicon_candidate())
    (item,) = await L.edit_items()
    assert await apply_confirmed_voice_item(L.ctx, item["id"], L.uid) is None  # ещё не подтверждено
    await owner.confirm(L.ctx, item["id"], L.uid)
    assert await apply_confirmed_voice_item(L.ctx, item["id"], L.uid) == 2
    repo = PassportsRepo(session, L.acc)
    active = await repo.get_active()
    assert active is not None and active.version == 2
    assert StylePassport.model_validate(active.body).hard_rules.banned_phrases == ["let's"]
    assert (await repo.get_by_version(1)).status == "archived"  # type: ignore[union-attr]
    assert await apply_confirmed_voice_item(L.ctx, item["id"], L.uid) is None  # повтор: без версии


# ---------------------------------------------------------------- класс C


async def lint_levels(L: Learn) -> list[str]:
    rules = await DbClassCRules().load(L.session, L.acc, "en")
    ctx = LintContext(class_c=tuple(rules))
    report = Linter().run(LintDraft(text="Let's dive into fees today"), ctx)
    return [f.level for f in report.findings if f.rule == "knowledge_c"]


async def test_soft_ban_becomes_lint_warning_immediately(session: AsyncSession) -> None:
    L = await make_learn(session)
    await two_edits(L)
    await proposed(L, humanness_candidate())
    (item,) = await L.edit_items()
    assert (item["status"], item["risk_class"]) == ("active", "C")
    assert await lint_levels(L) == ["warn"]


async def test_soft_ban_becomes_lint_error_after_confirm(session: AsyncSession) -> None:
    L = await make_learn(session)
    await two_edits(L)
    await proposed(L, humanness_candidate())
    (item,) = await L.edit_items()
    await owner.confirm(L.ctx, item["id"], L.uid)
    assert await lint_levels(L) == ["error"]


# ---------------------------------------------------------------- убранные приёмы


NUM = "12 failures last year. Here is what I learned"
NUM_AFTER = "Failures last year. Here is what I learned"


async def test_removed_applied_device_twice_lowers_fit(session: AsyncSession) -> None:
    L = await make_learn(session)
    item = await L.item()  # детектор: число в первой строке
    r1 = await L.review(before=NUM, after=NUM_AFTER, distance=0.1)
    await L.apply_item(item, r1)
    assert await detect_removed_devices(L.ctx, r1) == [item]
    L.llm.push(result())
    await propose(L.ctx)
    n = "SELECT count(*) FROM knowledge_evidence WHERE item_id = :i AND dimension = 'fit'"
    assert await L.scalar(n, i=item) == 0  # один раз — ещё не закономерность
    r2 = await L.review(before="7 mistakes I made. And what they cost", after="Mistakes I made")
    await L.apply_item(item, r2)
    L.llm.push(result())
    await propose(L.ctx)
    assert await L.scalar(n + " AND polarity = -1", i=item) == 1


async def test_unapplied_or_kept_device_is_not_removed(session: AsyncSession) -> None:
    L = await make_learn(session)
    item = await L.item()
    r = await L.review(before=NUM, after=NUM, distance=0.0)
    await L.apply_item(item, r)
    assert await detect_removed_devices(L.ctx, r) == []  # приём остался в тексте
    r2 = await L.review(before=NUM, after=NUM_AFTER)  # убрали, но знание не применялось
    assert await detect_removed_devices(L.ctx, r2) == []


# ---------------------------------------------------------------- подавление и отказы


async def test_rejected_meaning_suppressed_30_days(session: AsyncSession) -> None:
    L = await make_learn(session)
    await two_edits(L)
    await proposed(L, lexicon_candidate())
    (item,) = await L.edit_items()
    await owner.reject(L.ctx, item["id"], L.uid, delete=True)
    at = NOW + timedelta(minutes=1)
    await L.review(at=at)
    await L.review(at=at + timedelta(minutes=1), before=BEFORE2, after=AFTER2)
    L.ctx.clock.advance(3600)  # type: ignore[attr-defined]
    out = await proposed(L, lexicon_candidate())
    assert out == []
    assert [i["status"] for i in await L.edit_items()] == ["rejected"]


async def test_reject_reasons_feed_candidates(session: AsyncSession) -> None:
    L = await make_learn(session)
    await L.review(
        "reject",
        reason="not_my_voice",
        before="Official statement",
        after=None,
        distance=None,
        comment="слишком официально",
    )
    await L.review(
        "reject", reason="sounds_ai", before="Delve into synergy", after=None, distance=None
    )
    await L.review("reject", reason="off_topic", before="Offtopic post", after=None, distance=None)
    cand = lexicon_candidate(
        kind="anti_pattern",
        element="tone",
        statement="Тебе не подходит официальный и «умный» тон",
        guidance="Пиши проще, без канцелярита",
        rationale="Два отказа: не мой голос и звучит как AI",
    )
    out = await proposed(L, cand)
    assert len(out) == 1
    rej = L.llm.gen_calls[0].variables["rejections"]
    assert "not_my_voice" in rej and "sounds_ai" in rej and "слишком официально" in rej
    assert "off_topic" not in rej  # другие причины в голос не идут
    assert len(await L.edit_items()) == 1


# ---------------------------------------------------------------- запуск и эффект


async def test_trigger_after_five_reviews(session: AsyncSession) -> None:
    L = await make_learn(session)
    for _ in range(4):
        await L.review()
    assert await should_run(L.ctx) is False
    await L.review()
    assert await should_run(L.ctx) is True
    L.llm.push(result())
    await propose(L.ctx)
    assert await should_run(L.ctx) is False  # пачка обработана
    assert (
        await L.scalar(
            "SELECT count(*) FROM events WHERE account_id = :a AND type = 'learning.proposed'"
        )
        == 1
    )


async def test_trigger_weekly_needs_at_least_one_new_review(session: AsyncSession) -> None:
    L = await make_learn(session)
    assert await should_run(L.ctx) is False
    await L.review(at=NOW - timedelta(days=8))
    assert await should_run(L.ctx) is True  # одно ревью, но ему больше недели


async def decisions(L: Learn, approves: int, edits: int, start: timedelta) -> None:
    for k in range(approves):
        await L.review("approve", after=None, distance=0.0, at=NOW + start + timedelta(minutes=k))
    for k in range(edits):
        await L.review("edit", distance=0.3, at=NOW + start + timedelta(minutes=100 + k))


async def test_effect_measured_on_20_decisions_window(session: AsyncSession) -> None:
    L = await make_learn(session)
    item = await L.item(status="active")
    await decisions(L, 3, 12, timedelta(days=-10))  # старше окна «до»: не учитывается
    await decisions(L, 5, 15, timedelta(days=-3))  # 20 решений «до»: 5 без правок
    await emit(
        session,
        account_id=L.acc,
        type=EventType.KNOWLEDGE_OWNER_DECISION,
        actor="user",
        user_id=L.uid,
        entity=("knowledge_item", item),
        props={"item_id": str(item), "decision": "confirm"},
        ts=NOW - timedelta(days=1),
    )
    await decisions(L, 12, 10, timedelta(days=-0.5))  # «после»: первые 20 → 12 без правок
    e = await item_effect(L.ctx, item)
    assert (e.before_n, e.after_n) == (WINDOW, WINDOW)
    assert e.before_rate == 0.25 and e.after_rate == 0.6
    assert e.delta is not None and abs(e.delta - 0.35) < 1e-9
    assert e.enough is True


async def test_effect_not_enough_until_window_filled(session: AsyncSession) -> None:
    L = await make_learn(session)
    item = await L.item(status="active")
    await decisions(L, 2, 2, timedelta(days=-3))
    await emit(
        session,
        account_id=L.acc,
        type=EventType.KNOWLEDGE_OWNER_DECISION,
        actor="user",
        user_id=L.uid,
        entity=("knowledge_item", item),
        props={"item_id": str(item), "decision": "confirm"},
        ts=NOW - timedelta(days=1),
    )
    e = await item_effect(L.ctx, item)
    assert e.enough is False and e.after_n == 0 and e.delta is None
