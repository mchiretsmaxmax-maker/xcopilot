"""emit(): запись события, проверка типа и props."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.metrics.helpers import at, make_account, new_id
from xcopilot.metrics.errors import InvalidEventProps, UnknownEventType
from xcopilot.metrics.events import PROPS_SCHEMA, EventType, emit


async def _count(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM events"))).scalar_one())


async def test_emit_writes_event_in_same_transaction(session: AsyncSession) -> None:
    account_id = await make_account(session, "emit_tx")
    await session.commit()
    draft_id = new_id()
    event_id = await emit(
        session,
        account_id=account_id,
        type=EventType.REVIEW_APPROVED,
        actor="user",
        entity=("draft", draft_id),
        ts=at(10),
    )
    assert await _count(session) == 1
    row = (
        await session.execute(
            text("SELECT type, actor, entity_type, entity_id FROM events WHERE id = :i"),
            {"i": event_id},
        )
    ).one()
    assert tuple(row) == ("review.approved", "user", "draft", draft_id)
    await session.rollback()  # откат бизнес-операции убирает и событие
    assert await _count(session) == 0


async def test_emit_rejects_unknown_event_type(session: AsyncSession) -> None:
    account_id = await make_account(session, "emit_unknown")
    with pytest.raises(UnknownEventType):
        await emit(session, account_id=account_id, type="review.exploded")
    assert await _count(session) == 0


async def test_event_props_validated_by_schema(session: AsyncSession) -> None:
    account_id = await make_account(session, "emit_props")
    # пропущено обязательное поле
    with pytest.raises(InvalidEventProps):
        await emit(session, account_id=account_id, type=EventType.REVIEW_EDITED, props={})
    # лишнее поле
    with pytest.raises(InvalidEventProps):
        await emit(
            session,
            account_id=account_id,
            type=EventType.REVIEW_EDITED,
            props={"edit_distance": 0.2, "surprise": 1},
        )
    # неверный тип значения
    with pytest.raises(InvalidEventProps):
        await emit(
            session,
            account_id=account_id,
            type=EventType.REVIEW_EDITED,
            props={"edit_distance": "много"},
        )
    assert await _count(session) == 0
    await emit(
        session,
        account_id=account_id,
        type=EventType.REVIEW_EDITED,
        props={"edit_distance": 0.2},
    )
    assert await _count(session) == 1


def test_every_event_type_has_props_schema() -> None:
    assert set(PROPS_SCHEMA) == set(EventType)
    assert len(EventType) == 25
