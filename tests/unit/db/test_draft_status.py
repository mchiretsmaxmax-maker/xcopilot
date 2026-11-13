"""Машина статусов черновика."""

import pytest

from xcopilot.db.models.content import ALLOWED_TRANSITIONS, DraftStatus, assert_transition
from xcopilot.errors import InvalidTransition

S = DraftStatus

HAPPY_PATH = [
    (S.GENERATED, S.LINTED),
    (S.LINTED, S.JUDGED),
    (S.JUDGED, S.SHOWN),
    (S.SHOWN, S.APPROVED),
    (S.SHOWN, S.EDITED),
    (S.SHOWN, S.REJECTED),
    (S.APPROVED, S.PUBLISHED),
    (S.APPROVED, S.UNCONFIRMED),
    (S.EDITED, S.PUBLISHED),
    (S.EDITED, S.UNCONFIRMED),
    (S.UNCONFIRMED, S.PUBLISHED),
    (S.JUDGED, S.NEEDS_OWNER),
    (S.NEEDS_OWNER, S.SHOWN),
    (S.SHOWN, S.EXPIRED),
]

FORBIDDEN = [
    (S.GENERATED, S.PUBLISHED),
    (S.GENERATED, S.APPROVED),
    (S.LINTED, S.SHOWN),
    (S.REJECTED, S.APPROVED),
    (S.PUBLISHED, S.EDITED),
    (S.EXPIRED, S.SHOWN),
    (S.SHOWN, S.SHOWN),
]


def test_draft_status_transitions_enforced() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(DraftStatus)
    for old, new in HAPPY_PATH:
        assert_transition(old, new)
    for old, new in FORBIDDEN:
        with pytest.raises(InvalidTransition):
            assert_transition(old, new)
    for terminal in (S.PUBLISHED, S.REJECTED, S.EXPIRED):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
