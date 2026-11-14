"""Сиды «Мозга» (ТЗ v3.1, раздел 2.1): стартовые словари taxonomy_terms и сегменты-шаблоны
в системном аккаунте global.

Берётся только то, что есть в документе: ключи словарей; названия — где документ их даёт
(жанры, цели, форматы, сегменты); определения — только у форматов (таблица `kind`).
Остальные определения пустые — их пишет этап 7A (ADR 0015). Сегменты с параметром
(«по цели», «ниша», «язык», «жанр») — этап 9C.

Revision ID: 0005
Revises: 0004
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GLOBAL = "(SELECT id FROM accounts WHERE kind = 'global' AND handle = 'global')"

# (facet, key, title, description) — порядок и значения из «Стартовые словари» и 2.1.
_KEYS = {
    "hook_type": "number_personal, number_fact, contrarian, question, story_open, confession,"
    " bold_claim, how_to, list_promise, curiosity_gap, callout, news_peg, quote_react,"
    " plain_statement",
    "structure": "single_punch, claim_proof, problem_solution, story_arc, list, before_after,"
    " contrast_pair, step_by_step, observation_takeaway, qa, thread_hook_body_cta",
    "device": "specific_number, personal_proof, open_loop, pattern_break, short_lines,"
    " repetition, analogy, concrete_example, self_irony, direct_address, social_proof, stakes,"
    " visual_break, cliffhanger_line, receipts",
    "archetype": "alpha, hot_take, lifestyle, meme, educational, news_commentary, story,"
    " announcement, question_to_audience",
    "closer": "none, punchline, question, cta_reply, cta_follow, cta_link, takeaway",
    "tone": "calm, confident, ironic, vulnerable, aggressive, playful, analytical, hype, dry",
}
# Названия жанров — из таблицы сегментов («инсайт, острое мнение, путь, мем, обучение,
# комментарий к новости, история»), целей — «охват, вовлечённость, ответы, сохранения».
_TITLES = {
    ("archetype", "alpha"): "инсайт",
    ("archetype", "hot_take"): "острое мнение",
    ("archetype", "lifestyle"): "путь",
    ("archetype", "meme"): "мем",
    ("archetype", "educational"): "обучение",
    ("archetype", "news_commentary"): "комментарий к новости",
    ("archetype", "story"): "история",
}
_FORMATS = (
    ("post", "пост", "одиночный пост"),
    ("thread", "тред", "тред (части — отдельные куски)"),
    ("reply", "ответ", "ответ (исходный пост в parent_source_id)"),
    ("quote", "цитата", "цитата с комментарием (исходный в parent_source_id)"),
    ("article", "статья", "статья или длинный разбор"),
)
_GOALS = (
    ("reach", "охват"),
    ("engagement", "вовлечённость"),
    ("replies", "ответы"),
    ("saves", "сохранения"),
)


def _terms() -> list[tuple[str, str, str, str]]:
    rows = [
        (facet, key, _TITLES.get((facet, key), key), "")
        for facet, keys in _KEYS.items()
        for key in (k.strip() for k in keys.split(","))
    ]
    rows += [("format", k, t, d) for k, t, d in _FORMATS]
    rows += [("goal", k, t, "") for k, t in _GOALS]
    return rows


_NOT_VOICE = ["humanness", "craft", "niche", "platform"]
# Сегменты с готовым фильтром из таблицы «Сегменты из ТЗ и добавленные».
SEGMENTS: tuple[tuple[str, str, dict[str, list[str]]], ...] = (
    ("voice", "Голос", {"layer": ["voice"]}),
    ("humanness", "Человечность", {"layer": ["humanness"]}),
    ("posts", "Посты", {"format": ["post"], "layer": _NOT_VOICE}),
    ("threads", "Треды", {"format": ["thread"], "layer": _NOT_VOICE}),
    ("replies", "Ответы", {"format": ["reply"], "layer": _NOT_VOICE}),
    ("quotes", "Цитаты и репосты", {"format": ["quote"], "layer": _NOT_VOICE}),
    ("hooks", "Хуки", {"element": ["hook"]}),
    ("packaging_timing", "Упаковка и время", {"element": ["packaging", "timing"]}),
    ("platform", "Механика X", {"layer": ["platform"]}),
)


def upgrade() -> None:
    conn = op.get_bind()
    for facet, key, title, description in _terms():
        conn.execute(
            sa.text(
                "INSERT INTO taxonomy_terms (account_id, facet, key, title, description,"
                f" status, since_version, created_by) SELECT {GLOBAL}, :f, :k, :t, :d,"
                " 'active', 1, 'seed' ON CONFLICT (account_id, facet, key) DO NOTHING"
            ),
            {"f": facet, "k": key, "t": title, "d": description},
        )
    for position, (key, title, flt) in enumerate(SEGMENTS):
        conn.execute(
            sa.text(
                "INSERT INTO segments (account_id, key, title, filter, position, is_default)"
                f" SELECT {GLOBAL}, :k, :t, CAST(:f AS jsonb), :p, true"
                " ON CONFLICT (account_id, key) DO NOTHING"
            ),
            {"k": key, "t": title, "f": json.dumps(flt), "p": position},
        )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM segments WHERE account_id = {GLOBAL} AND is_default"
        f" AND key IN ({', '.join(repr(k) for k, _, _ in SEGMENTS)})"
    )
    op.execute(f"DELETE FROM taxonomy_terms WHERE account_id = {GLOBAL} AND created_by = 'seed'")
