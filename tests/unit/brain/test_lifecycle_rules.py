"""Правила переходов (ТЗ v3.1, раздел 2.4): чистая функция над снимком знания."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from xcopilot.brain.config import load_brain_config
from xcopilot.brain.lifecycle import ItemState, evaluate

CFG = load_brain_config()
NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)

READY = ItemState(
    item_id=uuid4(),
    status="candidate",
    risk_class="B",
    eff_conf=0.57,
    fit_conf=0.43,
    quality=0.51,
    evidence_n=3,
    independent_authors=3,
    voice_compat=0.8,
    status_since=NOW - timedelta(days=1),
)


def to(state: ItemState) -> str | None:
    t = evaluate(state, CFG, NOW)
    return None if t is None else t.to_status


def test_auto_probation_requires_all_conditions() -> None:
    t = evaluate(READY, CFG, NOW)
    assert t is not None and (t.to_status, t.rule) == ("probation", "auto_probation")
    # два своих подтверждения заменяют двух авторов
    assert to(replace(READY, independent_authors=1, own_support=2)) == "probation"
    blockers = [
        replace(READY, eff_conf=0.54),
        replace(READY, evidence_n=2),
        replace(READY, independent_authors=1, own_support=1),
        replace(READY, voice_compat=0.59),
        replace(READY, voice_compat=None),
        replace(READY, open_conflict=True),
        replace(READY, auto_probations_week=5),
        replace(READY, risk_class="C"),
    ]
    for state in blockers:
        assert to(state) is None, state


def test_class_a_never_auto_promoted() -> None:
    strong = replace(READY, risk_class="A", eff_conf=0.9, evidence_n=20, own_support=5)
    assert to(strong) is None


def test_weekly_auto_probation_limit() -> None:
    assert to(replace(READY, auto_probations_week=4)) == "probation"
    assert to(replace(READY, auto_probations_week=5)) is None


def test_promote_by_two_approvals() -> None:
    probation = replace(READY, status="probation", approvals_clean=2)
    t = evaluate(probation, CFG, NOW)
    assert t is not None and (t.to_status, t.rule) == ("active", "promote_by_reviews")
    assert to(replace(probation, approvals_clean=1)) is None
    assert to(replace(probation, voice_rejections=1)) != "active"


def test_promote_by_own_evidence() -> None:
    probation = replace(READY, status="probation", own_support=2)
    t = evaluate(probation, CFG, NOW)
    assert t is not None and (t.to_status, t.rule) == ("active", "promote_by_own_evidence")
    assert to(replace(probation, own_contra=1)) is None


def test_weaken_on_low_quality() -> None:
    active = replace(READY, status="active", quality=0.39)
    t = evaluate(active, CFG, NOW)
    assert t is not None and (t.to_status, t.rule) == ("weakened", "weaken_low_quality")
    assert to(replace(active, quality=0.45)) is None
    # опровержений на своих постах 2 и больше и больше подтверждений — тоже ослабление
    assert to(replace(active, quality=0.6, own_contra=2, own_support=1)) == "weakened"
    assert to(replace(active, quality=0.6, own_contra=2, own_support=2)) is None
    # ослабленное с quality ≥ 0.50 возвращается в работу
    assert to(replace(active, status="weakened", quality=0.5)) == "active"


def test_archive_after_idle_weakened() -> None:
    weakened = replace(
        READY,
        status="weakened",
        quality=0.3,
        status_since=NOW - timedelta(days=40),
        last_support_at=NOW - timedelta(days=31),
    )
    t = evaluate(weakened, CFG, NOW)
    assert t is not None and (t.to_status, t.rule) == ("archived", "archive_stale")
    assert to(replace(weakened, last_support_at=NOW - timedelta(days=5))) is None
    assert to(replace(weakened, last_support_at=NOW - timedelta(days=5), quality=0.2)) == "archived"
    # испытание без единого применения 21 день — в архив
    idle = replace(READY, status="probation", status_since=NOW - timedelta(days=22))
    assert to(idle) == "archived"
    assert to(replace(idle, last_applied_at=NOW - timedelta(days=3))) is None


def test_low_fit_archives_as_not_mine() -> None:
    probation = replace(READY, status="probation", fit_conf=0.16, quality=0.4)
    t = evaluate(probation, CFG, NOW)
    assert t is not None
    assert (t.to_status, t.rule) == ("archived", "low_fit")
    assert "не моё" in t.reason
    assert t.payload["fit_conf"] == 0.16


def test_pinned_never_auto_archived() -> None:
    pinned = [
        replace(READY, status="active", owner_state="pinned", quality=0.1, fit_conf=0.1),
        replace(
            READY,
            status="weakened",
            owner_state="pinned",
            quality=0.1,
            last_support_at=NOW - timedelta(days=90),
        ),
        replace(READY, status="probation", owner_state="pinned", fit_conf=0.1),
    ]
    for state in pinned:
        assert to(state) not in ("weakened", "archived"), state
