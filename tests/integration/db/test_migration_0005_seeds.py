"""Сид-миграция 0005: стартовые словари taxonomy_terms и сегменты (ТЗ v3.1, раздел 2.1)."""

from alembic import command

from tests.dbutil import fetch
from xcopilot.db.migrate import alembic_config

GLOBAL = "(SELECT id FROM accounts WHERE kind = 'global' AND handle = 'global')"

EXPECTED_KEYS = {
    "hook_type": {
        "number_personal", "number_fact", "contrarian", "question", "story_open", "confession",
        "bold_claim", "how_to", "list_promise", "curiosity_gap", "callout", "news_peg",
        "quote_react", "plain_statement",
    },
    "structure": {
        "single_punch", "claim_proof", "problem_solution", "story_arc", "list", "before_after",
        "contrast_pair", "step_by_step", "observation_takeaway", "qa", "thread_hook_body_cta",
    },
    "device": {
        "specific_number", "personal_proof", "open_loop", "pattern_break", "short_lines",
        "repetition", "analogy", "concrete_example", "self_irony", "direct_address",
        "social_proof", "stakes", "visual_break", "cliffhanger_line", "receipts",
    },
    "archetype": {
        "alpha", "hot_take", "lifestyle", "meme", "educational", "news_commentary", "story",
        "announcement", "question_to_audience",
    },
    "closer": {"none", "punchline", "question", "cta_reply", "cta_follow", "cta_link", "takeaway"},
    "tone": {
        "calm", "confident", "ironic", "vulnerable", "aggressive", "playful", "analytical",
        "hype", "dry",
    },
    "format": {"post", "thread", "reply", "quote", "article"},
    "goal": {"reach", "engagement", "replies", "saves"},
}  # fmt: skip

EXPECTED_SEGMENTS = {
    "voice",
    "humanness",
    "posts",
    "threads",
    "replies",
    "quotes",
    "hooks",
    "packaging_timing",
    "platform",
}


def _terms(url: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in fetch(url, f"SELECT facet, key FROM taxonomy_terms WHERE account_id = {GLOBAL}"):
        out.setdefault(r["facet"], set()).add(r["key"])
    return out


def _counts(url: str) -> tuple[int, int]:
    [t] = fetch(url, "SELECT count(*) AS n FROM taxonomy_terms")
    [s] = fetch(url, "SELECT count(*) AS n FROM segments")
    return t["n"], s["n"]


def test_taxonomy_and_segment_seeds_up_and_down(empty_db_url: str) -> None:
    cfg = alembic_config(empty_db_url)
    command.upgrade(cfg, "head")
    assert _terms(empty_db_url) == EXPECTED_KEYS
    rows = fetch(
        empty_db_url,
        "SELECT status, created_by, since_version FROM taxonomy_terms GROUP BY 1, 2, 3",
    )
    assert [tuple(r) for r in rows] == [("active", "seed", 1)]
    segs = {
        r["key"]: r
        for r in fetch(
            empty_db_url,
            f"SELECT key, title, filter, is_default, position FROM segments"
            f" WHERE account_id = {GLOBAL}",
        )
    }
    assert set(segs) == EXPECTED_SEGMENTS
    assert all(r["is_default"] for r in segs.values())
    assert segs["voice"]["title"] == "Голос"
    assert '"voice"' in segs["voice"]["filter"]
    assert '"thread"' in segs["threads"]["filter"] and '"voice"' not in segs["threads"]["filter"]
    counts = _counts(empty_db_url)
    assert counts == (sum(len(v) for v in EXPECTED_KEYS.values()), len(EXPECTED_SEGMENTS))
    # повторный upgrade после downgrade не дублирует, downgrade чистит
    command.downgrade(cfg, "0004")
    assert _counts(empty_db_url) == (0, 0)
    command.upgrade(cfg, "head")
    assert _counts(empty_db_url) == counts
