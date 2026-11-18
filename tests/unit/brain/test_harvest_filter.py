"""R3 (ADR 0034, п. 3): фильтр мусора под постом — `junk_reason`, код без модели."""

from __future__ import annotations

from datetime import UTC, datetime

from xcopilot.brain.harvest import HarvestConfig, JunkCtx, junk_reason
from xcopilot.x.models import XMetrics, XPost

CFG = HarvestConfig.load()
GOOD = "Rollups will survive because fees keep dropping every single quarter"


def reply(
    text: str,
    *,
    handle: str = "bob",
    likes: int = 50,
    followers: int | None = 1000,
    lang: str | None = "en",
    author_id: str | None = None,
) -> XPost:
    return XPost(
        id="2",
        text=text,
        author_handle=handle,
        author_id=author_id,
        created_at=datetime(2026, 11, 18, tzinfo=UTC),
        source="fxtwitter",
        metrics=XMetrics(likes=likes),
        author_followers=followers,
        lang=lang,
    )


def ctx(*, fresh: bool = False) -> JunkCtx:
    return JunkCtx(own_handle="prima_labs", parent_handle="vitalik", fresh=fresh, config=CFG)


def test_junk_short_after_strip() -> None:
    assert junk_reason(reply("@a @b 🔥🔥 so true"), ctx()) == "short"
    assert junk_reason(reply("fees keep dropping so rollups win long term"), ctx()) is None


def test_junk_link_or_ticker_or_promo() -> None:
    assert junk_reason(reply(f"{GOOD} https://t.co/abc"), ctx()) == "promo"
    assert junk_reason(reply(f"{GOOD} $PEPE to the moon"), ctx()) == "promo"
    assert junk_reason(reply(f"{GOOD}, check my bio"), ctx()) == "promo"


def test_junk_emoji_only() -> None:
    assert junk_reason(reply("🔥🔥🔥 #wagmi #crypto !!!"), ctx()) == "empty"


def test_junk_bot_call() -> None:
    assert junk_reason(reply("@grok is this true?"), ctx()) == "bot_call"
    assert junk_reason(reply(f"@AskPerplexity explain this please {GOOD}"), ctx()) == "bot_call"


def test_junk_low_followers_and_likes() -> None:
    assert junk_reason(reply(GOOD, followers=10), ctx()) == "low_author"
    assert junk_reason(reply(GOOD, likes=2), ctx()) == "low_likes"
    assert junk_reason(reply(GOOD, likes=0), ctx(fresh=True)) is None  # свежий пост: порога нет


def test_junk_lang() -> None:
    assert junk_reason(reply(GOOD, lang="ja"), ctx()) == "lang"
    assert junk_reason(reply(GOOD, lang=None), ctx()) is None
    assert junk_reason(reply("Роллапы выживут, потому что комиссии падают", lang=None), ctx()) == (
        "lang"
    )


def test_junk_self_and_parent_author() -> None:
    assert junk_reason(reply(GOOD, handle="prima_labs"), ctx()) == "self"
    assert junk_reason(reply(GOOD, handle="Vitalik"), ctx()) == "self"
