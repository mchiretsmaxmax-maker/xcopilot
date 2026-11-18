"""«💬 Ответы»: короткие ссылки t.co и посты без текста (только картинка) — без пустого писателя."""

from __future__ import annotations

from tests.cards_stand import CardsStand
from tests.integration.bot.test_reply_mode import (  # noqa: F401
    IN_MODE,
    LINK,
    POST_ID,
    POST_TEXT,
    FakeReader,
    _generate_payloads,
    _push,
    reader,
    st,
)
from xcopilot.bot import texts
from xcopilot.x.models import XMedia, XPost

TCO = "https://t.co/AbCdEf1234"
PHOTO_ID = "2123053023652176342"


def _expander(mapping: dict[str, str]):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def expand(url: str) -> str | None:
        calls.append(url)
        return mapping.get(url)

    expand.calls = calls  # type: ignore[attr-defined]
    return expand


async def test_tco_link_from_owner_is_expanded_and_read(
    st: CardsStand,  # noqa: F811
    reader: FakeReader,  # noqa: F811
) -> None:
    st.deps.link_expander = _expander({TCO: LINK})
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(TCO)
    assert reader.calls == [POST_ID]
    [payload] = await _generate_payloads(st)
    assert payload["target"]["text"] == POST_TEXT
    assert payload["view"]["post_id"] == POST_ID


async def test_tco_link_not_expanded_asks_for_xcom_link(
    st: CardsStand,  # noqa: F811
    reader: FakeReader,  # noqa: F811
) -> None:
    st.deps.link_expander = _expander({})
    await st.owner.say("💬 Ответы")
    await st.owner.say(TCO)
    assert st.owner.last.text == texts.REPLY_SHORT_LINK_FAILED
    assert await st.count("jobs") == 0
    assert await st.owner.state() == IN_MODE


async def test_image_only_post_asks_for_text_not_writer(
    st: CardsStand,  # noqa: F811
    reader: FakeReader,  # noqa: F811
) -> None:
    reader.posts[PHOTO_ID] = XPost(
        id=PHOTO_ID,
        text="https://t.co/SjXqlQtnS9",
        author_handle="boldleonidas",
        created_at=None,
        source="fxtwitter",
        media=(XMedia(type="photo", url="https://pbs.twimg.com/media/x.jpg"),),
    )
    link = f"https://x.com/boldleonidas/status/{PHOTO_ID}"
    await st.owner.say("💬 Ответы")
    await st.owner.say(link)
    assert st.owner.last.text == texts.REPLY_MEDIA_ONLY
    assert await st.count("jobs") == 0
    _push(st)
    await st.owner.say("На картинке график BTC: дно медвежки приходит всё раньше")
    [payload] = await _generate_payloads(st)
    assert payload["target"]["text"].startswith("На картинке график BTC")
    assert payload["target"]["url"] == link
    assert payload["view"]["post_id"] == PHOTO_ID
