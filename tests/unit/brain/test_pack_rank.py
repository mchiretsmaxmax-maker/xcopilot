"""Отбор и ранжирование знаний (этап 9B): чистые функции без БД."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hypothesis import given
from hypothesis import strategies as st

from xcopilot.brain.rank import (
    Candidate,
    Scored,
    fill_slots,
    item_tokens,
    pick_probation,
    scope_match,
    score,
    shares_run,
    slots_for,
)
from xcopilot.brain.schemas import PackItem, TaskSpec

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
TASK = TaskSpec(format="post", lang="ru", niche="crypto", topic="про провалы")


def cand(
    *,
    kind: str = "hook_pattern",
    element: str = "hook",
    layer: str = "craft",
    scopes: dict[str, frozenset[str]] | None = None,
    vec: list[float] | None = None,
    quality: float = 0.6,
    owner_state: str = "none",
    status: str = "active",
    text: str | None = None,
    **kw: object,
) -> Candidate:
    iid = uuid4()
    body = text or f"совет {iid.hex}"
    return Candidate(
        item_id=iid,
        version=1,
        kind=kind,
        layer=layer,
        element=element,
        statement=f"наблюдение {body}",
        guidance=body,
        skeleton="[a] → [b]" if kind == "structure" else None,
        status=status,
        owner_state=owner_state,
        quality=quality,
        scopes=scopes or {},
        vec=vec if vec is not None else [1.0, 0.0, 0.0],
        **{"last_evidence_at": NOW, **kw},  # type: ignore[arg-type]
    )


def scored(items: list[Candidate], base: float = 1.0) -> list[Scored]:
    return [Scored(c, base - i * 0.01) for i, c in enumerate(items)]


def by_slot(items: list[PackItem]) -> dict[str, list[PackItem]]:
    out: dict[str, list[PackItem]] = {}
    for i in items:
        out.setdefault(i.slot, []).append(i)
    return out


def group(items: list[PackItem], name: str) -> list[PackItem]:
    """Знания в смысловом слоте ТЗ 4.2 (по виду и элементу)."""
    if name == "anti":
        return [i for i in items if i.slot == "anti"]
    core = [i for i in items if i.slot == "core"]
    return {
        "hook": [i for i in core if i.element == "hook" and i.kind != "structure"],
        "structure": [i for i in core if i.kind == "structure"],
        "device": [i for i in core if i.element == "device"],
        "closer": [i for i in core if i.element == "closer"],
        "platform": [
            i for i in core if i.layer == "platform" or i.element in ("packaging", "timing")
        ],
    }[name]


def full_set() -> list[Candidate]:
    return (
        [cand() for _ in range(4)]
        + [cand(kind="structure", element="structure") for _ in range(3)]
        + [cand(kind="thesis", element="device") for _ in range(4)]
        + [cand(kind="thesis", element="closer") for _ in range(2)]
        + [cand(kind="thesis", element="general", layer="platform") for _ in range(3)]
        + [cand(kind="anti_pattern", element="general") for _ in range(4)]
    )


# ----------------------------------------------------------------------------- scope_match
def test_item_without_scope_matches_any_task() -> None:
    assert scope_match(cand(), TASK) == 0.6
    assert scope_match(cand(), TaskSpec(format="reply", lang="en", topic="тема тема")) == 0.6


def test_scope_filter_excludes_other_format_and_lang() -> None:
    assert scope_match(cand(scopes={"format": frozenset({"thread"})}), TASK) is None
    assert scope_match(cand(scopes={"lang": frozenset({"en"})}), TASK) is None
    assert scope_match(cand(scopes={"niche": frozenset({"fitness"})}), TASK) is None
    exact = cand(scopes={"format": frozenset({"post", "thread"}), "lang": frozenset({"ru"})})
    # 5 измерений задачи нет archetype → format, goal, lang, niche: 1.0, 0.6, 1.0, 0.6
    assert math.isclose(scope_match(exact, TASK) or 0, (1.0 + 0.6 + 1.0 + 0.6) / 4)


def test_scope_ignores_dimensions_missing_in_task() -> None:
    task = TaskSpec(format="post", lang="ru", topic="про провалы")
    assert scope_match(cand(scopes={"niche": frozenset({"fitness"})}), task) == 0.6


# ----------------------------------------------------------------------------- score
def test_ranking_prefers_own_proven_and_confirmed() -> None:
    vec = [1.0, 0.0, 0.0]
    plain = score(cand(), TASK, vec, NOW)
    proven = score(cand(own_support=2, own_contra=1), TASK, vec, NOW)
    confirmed = score(cand(owner_state="confirmed"), TASK, vec, NOW)
    assert math.isclose(proven / plain, 1.10)
    assert math.isclose(confirmed / plain, 1.15)
    assert plain > 0
    # одно подтверждение на своих постах или перевес опровержений буст не даёт
    assert math.isclose(score(cand(own_support=1), TASK, vec, NOW), plain)
    assert math.isclose(score(cand(own_support=2, own_contra=2), TASK, vec, NOW), plain)


def test_score_formula_parts() -> None:
    vec = [1.0, 0.0, 0.0]
    c = cand(quality=0.5, vec=[1.0, 0.0, 0.0])
    rel = 0.45 * 1.0 + 0.55 * 0.6
    assert math.isclose(score(c, TASK, vec, NOW), rel * 0.5 * 1.0, rel_tol=1e-9)
    old = cand(quality=0.5, last_evidence_at=NOW - timedelta(days=180))
    assert math.isclose(score(old, TASK, vec, NOW) / score(c, TASK, vec, NOW), 0.925)
    orthogonal = cand(vec=[0.0, 1.0, 0.0])
    assert score(orthogonal, TASK, vec, NOW) < score(c, TASK, vec, NOW)


# ----------------------------------------------------------------------------- fill_slots
def test_slots_filled_by_element_and_kind() -> None:
    items = fill_slots(scored(full_set()), slots_for("post"), 10_000)
    assert [
        len(group(items, n)) for n in ("hook", "structure", "device", "closer", "platform")
    ] == [
        3,
        2,
        3,
        1,
        2,
    ]
    assert len(group(items, "anti")) == 3
    assert len(items) <= 15
    assert [i.rank for i in items] == list(range(1, len(items) + 1))


def test_reply_task_uses_reply_slots() -> None:
    items = fill_slots(scored(full_set()), slots_for("reply"), 10_000)
    assert len(group(items, "hook")) == 2
    assert len(group(items, "structure")) == 1
    assert len(group(items, "platform")) == 1


def test_pinned_included_first_max_5() -> None:
    pinned = [cand(owner_state="pinned", element="device", kind="thesis") for _ in range(7)]
    items = fill_slots(scored(full_set() + pinned), slots_for("post"), 10_000)
    assert [i.slot for i in items[:5]] == ["pinned"] * 5
    assert sum(i.slot == "pinned" for i in items) == 5
    assert len({i.item_id for i in items}) == len(items)


def test_slot_order_pinned_first_probation_last() -> None:
    items = fill_slots(scored(full_set()), slots_for("post"), 10_000)
    assert [i.slot for i in items][-3:] == ["anti"] * 3


def test_mmr_diversity_within_slot() -> None:
    near = [cand(vec=[1.0, 0.01 * i, 0.0]) for i in range(3)]
    other = cand(vec=[0.0, 1.0, 0.0], quality=0.5)
    pool = [Scored(c, 1.0 - 0.01 * i) for i, c in enumerate(near)] + [Scored(other, 0.9)]
    items = fill_slots(pool, {**slots_for("post"), "hook": 3}, 10_000)
    ids = {i.item_id for i in items}
    assert other.item_id in ids
    assert len(ids) == 3


def test_trimming_order_under_small_budget() -> None:
    pool = scored(
        [cand() for _ in range(3)]
        + [cand(kind="structure", element="structure") for _ in range(2)]
        + [cand(kind="thesis", element="device") for _ in range(3)]
        + [cand(kind="thesis", element="closer")]
        + [cand(kind="thesis", element="general", layer="platform") for _ in range(2)]
        + [cand(kind="anti_pattern", element="general") for _ in range(3)]
    )
    full = fill_slots(pool, slots_for("post"), 10_000)
    total = sum(item_tokens(i) for i in full)
    # порядок снятия: приёмы (с худшего), площадка, структура
    drop_order = [
        i.item_id
        for g in ("device", "platform", "structure")
        for i in sorted(group(full, g), key=lambda x: x.score)
    ]
    tokens = {i.item_id: item_tokens(i) for i in full}
    for k in range(1, 7):
        budget = total - sum(tokens[x] for x in drop_order[:k])
        got = fill_slots(pool, slots_for("post"), budget)
        assert {i.item_id for i in got} == {i.item_id for i in full} - set(drop_order[:k])
        assert sum(item_tokens(i) for i in got) <= budget


def test_fill_slots_empty() -> None:
    assert fill_slots([], slots_for("post"), 1500) == []


# ----------------------------------------------------------------------------- испытание
def test_pick_probation_least_tested_then_oldest() -> None:
    a = cand(status="probation", applied=2, probation_started_at=NOW - timedelta(days=9))
    b = cand(status="probation", applied=0, probation_started_at=NOW - timedelta(days=2))
    c = cand(status="probation", applied=0, probation_started_at=NOW - timedelta(days=5))
    assert pick_probation([a, b, c]) is c
    assert pick_probation([]) is None
    assert pick_probation([cand(status="active")]) is None


# ----------------------------------------------------------------------------- чужой текст
WORDS = st.text(alphabet="абвгдежзиклмн", min_size=3, max_size=8)


@given(foreign=st.lists(WORDS, min_size=6, max_size=40), start=st.integers(0, 30))
def test_pack_contains_no_foreign_verbatim_text(foreign: list[str], start: int) -> None:
    """Любой фрагмент чужого текста из 6 слов подряд распознаётся как перенос."""
    start = start % (len(foreign) - 5)
    leaked = "свой зачин " + " ".join(foreign[start : start + 6]) + " свой конец"
    assert shares_run(leaked, [" ".join(foreign)])
    assert not shares_run("совсем другие слова без общего", [" ".join(foreign)])


def test_shares_run_case_and_punctuation_insensitive() -> None:
    foreign = "Один, два! три? Четыре; пять — шесть семь"
    assert shares_run("а потом один два три четыре пять шесть", [foreign])
    assert not shares_run("один два три четыре пять", [foreign])
