"""Производство контента: темы, запуски, черновики, ревью, публикации."""

from enum import StrEnum


class DraftStatus(StrEnum):
    """Статусы черновика."""

    GENERATED = "generated"
    LINTED = "linted"
    JUDGED = "judged"
    SHOWN = "shown"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    PUBLISHED = "published"
    UNCONFIRMED = "unconfirmed"
    NEEDS_OWNER = "needs_owner"
    EXPIRED = "expired"


ALLOWED_TRANSITIONS: dict[DraftStatus, frozenset[DraftStatus]] = {}


def assert_transition(old: DraftStatus, new: DraftStatus) -> None:
    """Проверить переход статуса черновика."""
    raise NotImplementedError
