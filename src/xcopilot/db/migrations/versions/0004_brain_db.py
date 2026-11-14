# ruff: noqa: E501
"""«Мозг» (ТЗ v3.1, приложения A и B): authors, sources kind + stance, 16 новых таблиц,
триггер «голос — только из своих материалов», пересчёт весов, представления; lessons удалена.

Решения и отступления от приложения A — docs/adr/0015-brain-db.md.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "'own_post', 'liked_sample', 'competitor_post', 'file', 'chat', 'note'"
OLD_ORIGINS = "'manual', 'x_api', 'oembed', 'radar'"
SOURCES_CHECKS_OLD = (
    ("ck_sources_kind_allowed", f"kind IN ({OLD_KINDS})"),
    ("ck_sources_origin_allowed", f"origin IN ({OLD_ORIGINS})"),
)
SOURCES_CHECKS_NEW = (
    (
        "ck_sources_kind_allowed",
        "kind IN ('post', 'thread', 'reply', 'quote', 'repost', 'article', 'note', 'file', 'chat')",
    ),
    (
        "ck_sources_stance_allowed",
        "stance IN ('own', 'inspiration', 'competitor', 'anti_example', 'reference')",
    ),
    (
        "ck_sources_origin_allowed",
        "origin IN ('manual_text', 'link_oembed', 'x_api', 'screenshot', 'tg_forward', 'file',"
        " 'url_fetch', 'history_backfill', 'radar')",
    ),
    (
        "ck_sources_analysis_status_allowed",
        "analysis_status IN ('pending', 'analyzed', 'skipped', 'failed', 'quarantined')",
    ),
    (
        "ck_sources_quarantine_reason",
        "(analysis_status = 'quarantined') = (quarantine_reason IS NOT NULL)",
    ),
)

# Перенос старых значений (ADR 0015): own_post/liked_sample/competitor_post → post + stance;
# note/file/chat — вид прежний, stance = reference; manual → manual_text, oembed → link_oembed.
SOURCES_UP = """
ALTER TABLE sources DROP CONSTRAINT ck_sources_kind_allowed, DROP CONSTRAINT ck_sources_origin_allowed;
UPDATE sources SET
  stance = CASE kind WHEN 'own_post' THEN 'own' WHEN 'liked_sample' THEN 'inspiration'
                     WHEN 'competitor_post' THEN 'competitor' ELSE 'reference' END,
  kind   = CASE WHEN kind IN ('own_post', 'liked_sample', 'competitor_post') THEN 'post' ELSE kind END,
  origin = CASE origin WHEN 'manual' THEN 'manual_text' WHEN 'oembed' THEN 'link_oembed' ELSE origin END
"""

# Обратный перенос с потерями (ADR 0015): вид post/thread/reply/quote/repost → *_post по stance
# (anti_example → liked_sample, reference → note); article → note; новые каналы → manual
# (history_backfill → x_api).
SOURCES_DOWN = """
UPDATE sources SET
  kind = CASE
    WHEN kind IN ('note', 'file', 'chat') THEN kind
    WHEN kind = 'article' THEN 'note'
    WHEN stance = 'own' THEN 'own_post'
    WHEN stance = 'competitor' THEN 'competitor_post'
    WHEN stance IN ('inspiration', 'anti_example') THEN 'liked_sample'
    ELSE 'note' END,
  origin = CASE origin
    WHEN 'manual_text' THEN 'manual' WHEN 'link_oembed' THEN 'oembed'
    WHEN 'x_api' THEN 'x_api' WHEN 'radar' THEN 'radar' WHEN 'history_backfill' THEN 'x_api'
    ELSE 'manual' END
"""

EMBEDDING_CHECKS_UP = """
ALTER TABLE embedding_models
  ADD CONSTRAINT ck_embedding_models_dup_threshold_range CHECK (dup_threshold BETWEEN 0 AND 1),
  ADD CONSTRAINT ck_embedding_models_related_threshold_range CHECK (related_threshold BETWEEN 0 AND 1)
"""

SNAPSHOTS_DEFAULT_PARTITION = """
CREATE TABLE knowledge_snapshots_default PARTITION OF knowledge_snapshots DEFAULT;
COMMENT ON TABLE knowledge_snapshots_default IS 'Партиция DEFAULT снимков знаний (страховка, если месячной нет)'
"""

TRIGGER_FN = """
CREATE FUNCTION trg_voice_evidence_own_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_layer  text;
  v_stance text;
BEGIN
  SELECT layer INTO v_layer FROM knowledge_items WHERE id = NEW.item_id;
  IF v_layer = 'voice' AND NEW.source_id IS NOT NULL THEN
    SELECT stance INTO v_stance FROM sources WHERE id = NEW.source_id;
    IF v_stance IS DISTINCT FROM 'own' THEN
      RAISE EXCEPTION 'voice knowledge % accepts evidence only from own materials (source %, stance %)',
        NEW.item_id, NEW.source_id, v_stance USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
COMMENT ON FUNCTION trg_voice_evidence_own_only() IS 'Триггер: знание слоя voice принимает доказательства только из своих материалов (stance = own)';
CREATE TRIGGER voice_evidence_own_only BEFORE INSERT ON knowledge_evidence
  FOR EACH ROW EXECUTE FUNCTION trg_voice_evidence_own_only();
COMMENT ON TRIGGER voice_evidence_own_only ON knowledge_evidence IS 'Голос учится только на своих материалах: защита на уровне БД'
"""

RECOMPUTE_FN = """
CREATE FUNCTION knowledge_recompute_scores(
  p_account     uuid,
  p_item        uuid        DEFAULT NULL,
  p_now         timestamptz DEFAULT now(),
  p_foreign_cap numeric     DEFAULT 0.70
) RETURNS integer LANGUAGE sql AS $$
  WITH ev AS (
    SELECT e.item_id, e.dimension, e.polarity, e.basis, e.author_id, e.observed_at,
           e.weight * power(0.5,
             GREATEST(extract(epoch FROM (p_now - e.observed_at)), 0) / 86400.0
             / CASE i.layer WHEN 'platform' THEN 90 WHEN 'voice' THEN 365 ELSE 180 END) AS w
    FROM knowledge_evidence e
    JOIN knowledge_items i ON i.id = e.item_id
    WHERE e.account_id = p_account
      AND (p_item IS NULL OR e.item_id = p_item)
      AND e.created_at <= p_now
      AND (e.revoked_at IS NULL OR e.revoked_at > p_now)
  ), agg AS (
    SELECT i.id AS item_id, i.target_metric,
           1 + coalesce(sum(ev.w) FILTER (WHERE ev.dimension = 'eff' AND ev.polarity =  1), 0) AS ea,
           1 + coalesce(sum(ev.w) FILTER (WHERE ev.dimension = 'eff' AND ev.polarity = -1), 0) AS eb,
           2 + coalesce(sum(ev.w) FILTER (WHERE ev.dimension = 'fit' AND ev.polarity =  1), 0) AS fa,
           1 + coalesce(sum(ev.w) FILTER (WHERE ev.dimension = 'fit' AND ev.polarity = -1), 0) AS fb,
           count(*) FILTER (WHERE ev.dimension = 'eff' AND ev.polarity <> 0)                    AS n_eff,
           count(DISTINCT ev.author_id) FILTER (WHERE ev.dimension = 'eff' AND ev.polarity = 1) AS n_authors,
           count(*) FILTER (WHERE ev.basis = 'own_outcome' AND ev.polarity =  1)                AS own_s,
           count(*) FILTER (WHERE ev.basis = 'own_outcome' AND ev.polarity = -1)                AS own_c,
           count(ev.item_id) FILTER (WHERE ev.dimension = 'eff')                                AS occ,
           max(ev.observed_at)                                                                  AS last_at
    FROM knowledge_items i
    LEFT JOIN ev ON ev.item_id = i.id
    WHERE i.account_id = p_account AND (p_item IS NULL OR i.id = p_item)
    GROUP BY i.id, i.target_metric
  ), sc AS (
    SELECT a.*,
           GREATEST(ea / (ea + eb) - sqrt(ea * eb / (power(ea + eb, 2) * (ea + eb + 1))), 0) AS eff_lb,
           GREATEST(fa / (fa + fb) - sqrt(fa * fb / (power(fa + fb, 2) * (fa + fb + 1))), 0) AS fit_lb
    FROM agg a
  ), fin AS (
    SELECT s.*,
           CASE WHEN own_s + own_c = 0 THEN LEAST(eff_lb, p_foreign_cap) ELSE eff_lb END AS eff_c
    FROM sc s
  ), upd AS (
    UPDATE knowledge_items k SET
      eff_alpha = round(f.ea, 4), eff_beta = round(f.eb, 4),
      fit_alpha = round(f.fa, 4), fit_beta = round(f.fb, 4),
      eff_conf  = round(f.eff_c, 4),
      fit_conf  = round(f.fit_lb, 4),
      quality   = round(CASE WHEN f.target_metric = 'quality' THEN f.fit_lb
                             ELSE 0.6 * f.eff_c + 0.4 * f.fit_lb END, 4),
      evidence_n = f.n_eff, independent_authors = f.n_authors,
      own_support = f.own_s, own_contra = f.own_c, occurrences = f.occ,
      last_evidence_at = f.last_at, scores_updated_at = p_now
    FROM fin f
    WHERE k.id = f.item_id
    RETURNING 1
  )
  SELECT count(*)::integer FROM upd;
$$;
COMMENT ON FUNCTION knowledge_recompute_scores(uuid, uuid, timestamptz, numeric) IS
  'Пересчитывает веса знаний аккаунта из неотозванных доказательств со спадом по свежести. p_now в прошлом даёт состояние на ту дату'
"""

VIEWS = """
CREATE VIEW v_knowledge_usable AS
SELECT k.*
FROM knowledge_items k
WHERE k.status IN ('active', 'probation')
  AND k.owner_state <> 'disabled'
  AND NOT EXISTS (
    SELECT 1 FROM knowledge_links l
    WHERE l.status = 'open' AND l.relation = 'contradicts' AND k.id IN (l.from_item_id, l.to_item_id));
COMMENT ON VIEW v_knowledge_usable IS 'Знания, допущенные к применению в генерации';

CREATE VIEW v_knowledge_cards AS
SELECT k.id, k.account_id, k.kind, k.layer, k.element, k.statement, k.guidance, k.skeleton,
       k.target_metric, k.risk_class, k.status, k.owner_state, k.origin, k.version,
       k.eff_conf, k.fit_conf, k.quality,
       CASE WHEN k.quality >= 0.60 THEN 'high' WHEN k.quality >= 0.45 THEN 'medium' ELSE 'low' END AS confidence_label,
       k.evidence_n, k.independent_authors, k.own_support, k.own_contra, k.occurrences, k.last_evidence_at,
       coalesce((SELECT jsonb_object_agg(x.facet, x.vals)
                 FROM (SELECT s.facet, jsonb_agg(s.value ORDER BY s.value) AS vals
                       FROM knowledge_item_scopes s WHERE s.item_id = k.id GROUP BY s.facet) x), '{}'::jsonb) AS scopes,
       (SELECT count(*) FROM knowledge_links l
        WHERE l.status = 'open' AND l.relation = 'contradicts' AND k.id IN (l.from_item_id, l.to_item_id)) AS open_conflicts
FROM knowledge_items k
WHERE k.status <> 'merged';
COMMENT ON VIEW v_knowledge_cards IS 'Знание с областью действия, словесной уверенностью и числом открытых противоречий';

CREATE VIEW v_segment_items AS
SELECT s.account_id, s.key AS segment_key, s.title AS segment_title, s.position,
       k.id AS item_id, k.status, k.quality
FROM segments s
JOIN knowledge_items k ON k.account_id = s.account_id AND k.status NOT IN ('merged', 'rejected')
WHERE (NOT s.filter ? 'layer'   OR s.filter -> 'layer'   ? k.layer)
  AND (NOT s.filter ? 'element' OR s.filter -> 'element' ? k.element)
  AND (NOT s.filter ? 'kind'    OR s.filter -> 'kind'    ? k.kind)
  AND NOT EXISTS (
    SELECT 1
    FROM jsonb_each(s.filter) AS f(facet, vals)
    WHERE f.facet IN ('format', 'goal', 'niche', 'lang', 'archetype')
      AND EXISTS     (SELECT 1 FROM knowledge_item_scopes sc WHERE sc.item_id = k.id AND sc.facet = f.facet)
      AND NOT EXISTS (SELECT 1 FROM knowledge_item_scopes sc WHERE sc.item_id = k.id AND sc.facet = f.facet AND f.vals ? sc.value));
COMMENT ON VIEW v_segment_items IS 'Какие знания видны в каком сегменте';

CREATE VIEW v_knowledge_evidence_summary AS
SELECT e.account_id, e.item_id, e.dimension, e.basis, e.format,
       count(*) FILTER (WHERE e.polarity =  1) AS supports,
       count(*) FILTER (WHERE e.polarity = -1) AS contradicts,
       count(*) FILTER (WHERE e.polarity =  0) AS neutral,
       round(coalesce(sum(e.weight) FILTER (WHERE e.polarity =  1), 0), 3) AS support_weight,
       round(coalesce(sum(e.weight) FILTER (WHERE e.polarity = -1), 0), 3) AS contra_weight,
       max(e.observed_at) AS last_observed_at
FROM knowledge_evidence e
WHERE e.revoked_at IS NULL
GROUP BY e.account_id, e.item_id, e.dimension, e.basis, e.format;
COMMENT ON VIEW v_knowledge_evidence_summary IS 'За и против по основаниям и форматам';

CREATE VIEW v_material_best_signal AS
SELECT DISTINCT ON (m.source_id) m.*
FROM material_signals m
ORDER BY m.source_id,
         CASE m.origin WHEN 'own_sync' THEN 1 WHEN 'x_api' THEN 2 WHEN 'screenshot' THEN 3
                       WHEN 'manual_numbers' THEN 4 WHEN 'owner_label' THEN 5 ELSE 6 END,
         m.observed_at DESC;
COMMENT ON VIEW v_material_best_signal IS 'Одно наблюдение на материал, по которому считается исход';

CREATE FUNCTION knowledge_state_on(p_account uuid, p_date date)
RETURNS SETOF knowledge_snapshots LANGUAGE sql STABLE AS $$
  SELECT s.*
  FROM knowledge_snapshots s
  WHERE s.account_id = p_account
    AND s.snapshot_date = (SELECT max(x.snapshot_date) FROM knowledge_snapshots x
                           WHERE x.account_id = p_account AND x.snapshot_date <= p_date);
$$;
COMMENT ON FUNCTION knowledge_state_on(uuid, date) IS 'Все знания аккаунта в состоянии на конец указанного дня'
"""

BRAIN_SQL = (TRIGGER_FN, RECOMPUTE_FN, VIEWS)
BRAIN_SQL_DOWN = (
    "DROP FUNCTION knowledge_state_on(uuid, date)",
    "DROP VIEW v_material_best_signal, v_knowledge_evidence_summary, v_segment_items,"
    " v_knowledge_cards, v_knowledge_usable",
    "DROP FUNCTION knowledge_recompute_scores(uuid, uuid, timestamptz, numeric)",
    "DROP TRIGGER voice_evidence_own_only ON knowledge_evidence",
    "DROP FUNCTION trg_voice_evidence_own_only()",
)


def _statements(sql: str) -> list[str]:
    """Разбить SQL на отдельные команды по «;» вне тел функций $$…$$ (asyncpg — по одной)."""
    out: list[str] = []
    buf: list[str] = []
    in_body = False
    i = 0
    while i < len(sql):
        if sql.startswith("$$", i):
            in_body = not in_body
            buf.append("$$")
            i += 2
            continue
        ch = sql[i]
        if ch == ";" and not in_body:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [x for x in out if x]


def _run(sql: str) -> None:
    for stmt in _statements(sql):
        op.execute(stmt)


def upgrade() -> None:
    op.create_table(
        "authors",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column(
            "handle", sa.String(), nullable=False, comment="Хэндл в X без @, в нижнем регистре"
        ),
        sa.Column(
            "x_user_id", sa.String(), nullable=True, comment="ID пользователя X, если известен"
        ),
        sa.Column("display_name", sa.String(), nullable=True, comment="Отображаемое имя"),
        sa.Column(
            "is_self",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Это сам владелец аккаунта. Одна такая строка на аккаунт",
        ),
        sa.Column(
            "followers",
            sa.Integer(),
            nullable=True,
            comment="Число подписчиков на момент последнего наблюдения",
        ),
        sa.Column(
            "followers_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда наблюдали число подписчиков",
        ),
        sa.Column(
            "followers_origin",
            sa.String(),
            nullable=True,
            comment="Откуда число: x_api, screenshot, manual",
        ),
        sa.Column(
            "niche", sa.String(), nullable=True, comment="Ниша автора (ключ таксономии niche)"
        ),
        sa.Column(
            "trust",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="-1 — заблокирован (материалы не учатся), 0 — обычный, 1 — доверенный",
        ),
        sa.Column("note", sa.String(), nullable=True, comment="Заметка владельца"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Изменено",
        ),
        sa.CheckConstraint(
            "followers_origin IN ('x_api', 'screenshot', 'manual')",
            name=op.f("ck_authors_followers_origin_allowed"),
        ),
        sa.CheckConstraint(
            "handle = lower(handle) AND handle !~ '^@'", name=op.f("ck_authors_handle_normalized")
        ),
        sa.CheckConstraint("followers >= 0", name=op.f("ck_authors_followers_nonneg")),
        sa.CheckConstraint("trust IN (-1, 0, 1)", name=op.f("ck_authors_trust_allowed")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_authors_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authors")),
        sa.UniqueConstraint("account_id", "handle", name=op.f("uq_authors_account_id_handle")),
        sa.UniqueConstraint("account_id", "id", name=op.f("uq_authors_account_id_id")),
        comment="Авторы материалов. Нужны для нормировки охвата, независимости доказательств и блокировки источников",
    )
    op.create_index(
        "uq_authors_self",
        "authors",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("is_self"),
    )
    # --- sources: kind + stance (ТЗ v3.1, приложение A, раздел 2) ---
    op.add_column(
        "sources",
        sa.Column(
            "stance",
            sa.String(),
            nullable=True,
            comment="Отношение владельца: own — моё, inspiration — нравится, competitor — конкурент, anti_example — так не надо, reference — материал и факты",
        ),
    )
    op.add_column(
        "sources", sa.Column("author_id", sa.Uuid(), nullable=True, comment="Автор материала")
    )
    op.add_column(
        "sources",
        sa.Column(
            "parent_source_id",
            sa.Uuid(),
            nullable=True,
            comment="Пост, на который это ответ или цитата. Нужен как контекст разбора",
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "thread_root_id",
            sa.Uuid(),
            nullable=True,
            comment="Первый пост треда, если материал — часть треда",
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "posted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда пост вышел в X (не когда попал в базу). От него считается свежесть доказательств",
        ),
    )
    op.add_column(
        "sources",
        sa.Column("lang", sa.String(), nullable=True, comment="Язык материала (ISO 639-1)"),
    )
    op.add_column(
        "sources",
        sa.Column(
            "owner_note",
            sa.String(),
            nullable=True,
            comment="Что владелец написал про материал при отправке",
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "analysis_status",
            sa.String(),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="Состояние разбора: pending, analyzed, skipped, failed, quarantined",
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "quarantine_reason",
            sa.String(),
            nullable=True,
            comment="Почему материал в карантине (инъекция, не контент, автор заблокирован)",
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "removed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Владелец убрал материал. Его доказательства отзываются, веса пересчитываются",
        ),
    )
    op.alter_column(
        "sources",
        "kind",
        existing_type=sa.VARCHAR(),
        comment="Что это физически: post, thread, reply, quote, repost, article, note, file, chat",
        existing_comment="Что это: own_post / liked_sample / competitor_post / file / chat / note",
        existing_nullable=False,
    )
    op.alter_column(
        "sources",
        "origin",
        existing_type=sa.VARCHAR(),
        comment="Канал получения: manual_text, link_oembed, x_api, screenshot, tg_forward, file, url_fetch, history_backfill, radar",
        existing_comment="Откуда: manual / x_api / oembed / radar",
        existing_nullable=False,
    )
    _run(SOURCES_UP)
    op.alter_column("sources", "stance", existing_type=sa.String(), nullable=False)
    for name, sql in SOURCES_CHECKS_NEW:
        op.create_check_constraint(op.f(name), "sources", sql)
    op.create_index(
        "ix_sources_author",
        "sources",
        ["account_id", "author_id", sa.literal_column("posted_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_sources_learning",
        "sources",
        ["account_id", "stance", "kind", "analysis_status"],
        unique=False,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index(
        "ix_sources_parent",
        "sources",
        ["account_id", "parent_source_id"],
        unique=False,
        postgresql_where=sa.text("parent_source_id IS NOT NULL"),
    )
    op.create_unique_constraint(op.f("uq_sources_account_id_id"), "sources", ["account_id", "id"])
    op.create_foreign_key(
        "fk_sources_author", "sources", "authors", ["account_id", "author_id"], ["account_id", "id"]
    )
    op.create_foreign_key(
        "fk_sources_parent",
        "sources",
        "sources",
        ["account_id", "parent_source_id"],
        ["account_id", "id"],
    )
    op.create_foreign_key(
        "fk_sources_thread",
        "sources",
        "sources",
        ["account_id", "thread_root_id"],
        ["account_id", "id"],
    )
    # --- новые таблицы ---
    op.create_table(
        "knowledge_items",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column(
            "account_id", sa.Uuid(), nullable=False, comment="Тенант (global для общих шаблонов)"
        ),
        sa.Column(
            "kind",
            sa.String(),
            nullable=False,
            comment="Тип знания: thesis, hook_pattern, structure, anti_pattern, lexicon, exemplar",
        ),
        sa.Column(
            "layer",
            sa.String(),
            nullable=False,
            comment="Слой: voice — только мой голос; humanness — живой текст против AI; craft — переносимое ремесло; niche — конвенции ниши; platform — механика X",
        ),
        sa.Column(
            "element",
            sa.String(),
            nullable=False,
            comment="К какой части текста относится: hook, structure, device, closer, tone, lexicon, packaging, timing, general",
        ),
        sa.Column(
            "statement",
            sa.String(),
            nullable=False,
            comment="Проверяемое утверждение для владельца, 15–240 знаков",
        ),
        sa.Column(
            "guidance",
            sa.String(),
            nullable=False,
            comment="Как применять — короткое указание для модели-автора, 10–200 знаков",
        ),
        sa.Column(
            "skeleton",
            sa.String(),
            nullable=True,
            comment="Шаблон со слотами для structure и exemplar. Чужой дословный текст сюда не попадает",
        ),
        sa.Column(
            "target_metric",
            sa.String(),
            server_default=sa.text("'engagement'"),
            nullable=False,
            comment="На какую цель работает знание: reach, engagement, replies, saves; quality — про качество текста, без исхода",
        ),
        sa.Column(
            "detector",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text('\'{"type": "none"}\'::jsonb'),
            nullable=False,
            comment="Как распознать знание в тексте: {type: feature, all: [{path, op, value}]} — по полям разбора; {type: rubric, question} — вопрос судье; {type: none}",
        ),
        sa.Column(
            "risk_class",
            sa.CHAR(length=1),
            nullable=False,
            comment="A — меняет голос, только с подтверждением; B — ремесло, испытание в одном варианте; C — мягкий запрет, предупреждение линтера",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'candidate'"),
            nullable=False,
            comment="Жизненный цикл: candidate, probation, active, weakened, conflicted, archived, rejected, merged",
        ),
        sa.Column(
            "owner_state",
            sa.String(),
            server_default=sa.text("'none'"),
            nullable=False,
            comment="Решение владельца: none, confirmed, pinned (всегда в пакете), disabled (не применять)",
        ),
        sa.Column(
            "origin",
            sa.String(),
            nullable=False,
            comment="Откуда появилось: materials, edits (правки черновиков), own_metrics, owner (ввёл сам), seed",
        ),
        sa.Column(
            "parent_item_id",
            sa.Uuid(),
            nullable=True,
            comment="Общий шаблон из аккаунта global, от которого создана копия",
        ),
        sa.Column(
            "merged_into",
            sa.Uuid(),
            nullable=True,
            comment="В какое знание влито (для status = merged)",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Номер действующей версии текста",
        ),
        sa.Column(
            "eff_alpha",
            sa.Numeric(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Результативность: априор 1 + сумма весов подтверждений",
        ),
        sa.Column(
            "eff_beta",
            sa.Numeric(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Результативность: априор 1 + сумма весов опровержений",
        ),
        sa.Column(
            "fit_alpha",
            sa.Numeric(),
            server_default=sa.text("2"),
            nullable=False,
            comment="Совместимость с владельцем: априор 2 + сумма весов принятий",
        ),
        sa.Column(
            "fit_beta",
            sa.Numeric(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Совместимость: априор 1 + сумма весов отказов",
        ),
        sa.Column(
            "eff_conf",
            sa.Numeric(),
            server_default=sa.text("0.2113"),
            nullable=False,
            comment="Уверенность в результативности: среднее минус одно стандартное отклонение, 0..1",
        ),
        sa.Column(
            "fit_conf",
            sa.Numeric(),
            server_default=sa.text("0.4310"),
            nullable=False,
            comment="Уверенность в совместимости, 0..1",
        ),
        sa.Column(
            "quality",
            sa.Numeric(),
            server_default=sa.text("0.2992"),
            nullable=False,
            comment="Итоговое качество для ранжирования: 0.6·eff_conf + 0.4·fit_conf (для target_metric = quality — только fit_conf)",
        ),
        sa.Column(
            "evidence_n",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Число доказательств результативности с исходом",
        ),
        sa.Column(
            "independent_authors",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Число разных авторов среди подтверждений",
        ),
        sa.Column(
            "own_support",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Подтверждений на своих постах",
        ),
        sa.Column(
            "own_contra",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Опровержений на своих постах",
        ),
        sa.Column(
            "occurrences",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Сколько раз знание встретилось в материалах вообще",
        ),
        sa.Column(
            "last_evidence_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Дата самого свежего доказательства",
        ),
        sa.Column(
            "scores_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда пересчитаны веса",
        ),
        sa.Column(
            "probation_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда знание взято на испытание",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Изменено",
        ),
        sa.CheckConstraint(
            "(status = 'merged') = (merged_into IS NOT NULL)",
            name=op.f("ck_knowledge_items_merged_into"),
        ),
        sa.CheckConstraint(
            "detector ->> 'type' IN ('feature', 'rubric', 'none')",
            name=op.f("ck_knowledge_items_detector_type"),
        ),
        sa.CheckConstraint(
            "element IN ('hook', 'structure', 'device', 'closer', 'tone', 'lexicon', 'packaging', 'timing', 'general')",
            name=op.f("ck_knowledge_items_element_allowed"),
        ),
        sa.CheckConstraint(
            "kind <> 'lexicon' OR layer = 'voice'", name=op.f("ck_knowledge_items_lexicon_is_voice")
        ),
        sa.CheckConstraint(
            "kind IN ('thesis', 'hook_pattern', 'structure', 'anti_pattern', 'lexicon', 'exemplar')",
            name=op.f("ck_knowledge_items_kind_allowed"),
        ),
        sa.CheckConstraint(
            "layer <> 'voice' OR risk_class = 'A'", name=op.f("ck_knowledge_items_voice_is_class_a")
        ),
        sa.CheckConstraint(
            "layer IN ('voice', 'humanness', 'craft', 'niche', 'platform')",
            name=op.f("ck_knowledge_items_layer_allowed"),
        ),
        sa.CheckConstraint(
            "origin IN ('materials', 'edits', 'own_metrics', 'owner', 'seed')",
            name=op.f("ck_knowledge_items_origin_allowed"),
        ),
        sa.CheckConstraint(
            "owner_state IN ('none', 'confirmed', 'pinned', 'disabled')",
            name=op.f("ck_knowledge_items_owner_state_allowed"),
        ),
        sa.CheckConstraint(
            "risk_class IN ('A', 'B', 'C')", name=op.f("ck_knowledge_items_risk_class_allowed")
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'probation', 'active', 'weakened', 'conflicted', 'archived', 'rejected', 'merged')",
            name=op.f("ck_knowledge_items_status_allowed"),
        ),
        sa.CheckConstraint(
            "target_metric IN ('reach', 'engagement', 'replies', 'saves', 'quality')",
            name=op.f("ck_knowledge_items_target_metric_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(guidance) BETWEEN 10 AND 200", name=op.f("ck_knowledge_items_guidance_len")
        ),
        sa.CheckConstraint(
            "char_length(skeleton) <= 600", name=op.f("ck_knowledge_items_skeleton_len")
        ),
        sa.CheckConstraint(
            "char_length(statement) BETWEEN 15 AND 240",
            name=op.f("ck_knowledge_items_statement_len"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "merged_into"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_items_merged_into",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_knowledge_items_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_items")),
        sa.UniqueConstraint("account_id", "id", name=op.f("uq_knowledge_items_account_id_id")),
        comment="Единица знания: тезис, паттерн хука, структура, анти-паттерн, словарь, пример-эталон. Текущее состояние; история текста — в knowledge_item_versions, история весов — в knowledge_snapshots",
    )
    op.create_index(
        "ix_knowledge_items_pack",
        "knowledge_items",
        ["account_id", "layer", "element", sa.literal_column("quality DESC")],
        unique=False,
        postgresql_where=sa.text("status IN ('active', 'probation') AND owner_state <> 'disabled'"),
    )
    op.create_index(
        "ix_knowledge_items_parent",
        "knowledge_items",
        ["parent_item_id"],
        unique=False,
        postgresql_where=sa.text("parent_item_id IS NOT NULL"),
    )
    op.create_index(
        "ix_knowledge_items_status",
        "knowledge_items",
        ["account_id", "status", sa.literal_column("updated_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_items_trgm",
        "knowledge_items",
        ["statement"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"statement": "gin_trgm_ops"},
    )
    op.create_table(
        "knowledge_packs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column(
            "consumer", sa.String(), nullable=False, comment="Кому выдан: writer, export, mcp, eval"
        ),
        sa.Column(
            "task",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Задача (TaskSpec): формат, цель, ниша, язык, жанр, тема",
        ),
        sa.Column(
            "schema_version", sa.Integer(), nullable=False, comment="Версия схемы KnowledgePack"
        ),
        sa.Column(
            "taxonomy_version",
            sa.Integer(),
            nullable=False,
            comment="Версия таксономии на момент сборки",
        ),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Состав: [{item_id, version, slot, rank, score}]",
        ),
        sa.Column(
            "token_count", sa.Integer(), nullable=False, comment="Оценка размера пакета в токенах"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.CheckConstraint(
            "consumer IN ('writer', 'export', 'mcp', 'eval')",
            name=op.f("ck_knowledge_packs_consumer_allowed"),
        ),
        sa.CheckConstraint("token_count >= 0", name=op.f("ck_knowledge_packs_token_count_nonneg")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_knowledge_packs_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_packs")),
        sa.UniqueConstraint("account_id", "id", name=op.f("uq_knowledge_packs_account_id_id")),
        comment="Пакет знаний, выданный под задачу. Хранит id и порядок знаний, чтобы любой черновик можно было объяснить и воспроизвести",
    )
    op.create_index(
        "ix_knowledge_packs_time",
        "knowledge_packs",
        ["account_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "knowledge_snapshots",
        sa.Column(
            "snapshot_date", sa.Date(), nullable=False, comment="Дата снимка (по поясу аккаунта)"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column("status", sa.String(), nullable=False, comment="Статус на дату"),
        sa.Column("owner_state", sa.String(), nullable=False, comment="Решение владельца на дату"),
        sa.Column("version", sa.Integer(), nullable=False, comment="Версия текста на дату"),
        sa.Column("layer", sa.String(), nullable=False, comment="Слой знания"),
        sa.Column("eff_alpha", sa.Numeric(), nullable=False, comment="eff_alpha на дату"),
        sa.Column("eff_beta", sa.Numeric(), nullable=False, comment="eff_beta на дату"),
        sa.Column("fit_alpha", sa.Numeric(), nullable=False, comment="fit_alpha на дату"),
        sa.Column("fit_beta", sa.Numeric(), nullable=False, comment="fit_beta на дату"),
        sa.Column("eff_conf", sa.Numeric(), nullable=False, comment="eff_conf на дату"),
        sa.Column("fit_conf", sa.Numeric(), nullable=False, comment="fit_conf на дату"),
        sa.Column("quality", sa.Numeric(), nullable=False, comment="quality на дату"),
        sa.Column(
            "evidence_n", sa.Integer(), nullable=False, comment="Число доказательств на дату"
        ),
        sa.Column(
            "own_support",
            sa.Integer(),
            nullable=False,
            comment="Подтверждений на своих постах на дату",
        ),
        sa.Column(
            "own_contra",
            sa.Integer(),
            nullable=False,
            comment="Опровержений на своих постах на дату",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_knowledge_snapshots_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_date", "account_id", "item_id", name=op.f("pk_knowledge_snapshots")
        ),
        comment="Состояние каждого знания на конец дня. Партиции по месяцам. Источник метрик «Мозга», сравнения «неделя к неделе» и отката на дату",
        postgresql_partition_by="RANGE (snapshot_date)",
    )
    op.create_index(
        "ix_knowledge_snapshots_item",
        "knowledge_snapshots",
        ["account_id", "item_id", sa.literal_column("snapshot_date DESC")],
        unique=False,
    )
    op.create_table(
        "segments",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("key", sa.String(), nullable=False, comment="Ключ сегмента"),
        sa.Column("title", sa.String(), nullable=False, comment="Название в боте"),
        sa.Column(
            "filter",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Фильтр: {layer: [...], element: [...], kind: [...], format: [...], goal: [...], niche: [...], lang: [...]}. Пустой ключ — без ограничения",
        ),
        sa.Column(
            "position",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Порядок в меню",
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Создан системой при онбординге",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]{1,39}$'", name=op.f("ck_segments_key_format")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_segments_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_segments")),
        sa.UniqueConstraint("account_id", "key", name=op.f("uq_segments_account_id_key")),
        comment="Сегмент — это фильтр по слою, элементу и области действия знаний. Знание попадает в несколько сегментов без дублирования",
    )
    op.create_table(
        "taxonomy_terms",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column(
            "account_id", sa.Uuid(), nullable=False, comment="Тенант (global для общих значений)"
        ),
        sa.Column("facet", sa.String(), nullable=False, comment="Какой словарь"),
        sa.Column("key", sa.String(), nullable=False, comment="Ключ значения, латиницей"),
        sa.Column("title", sa.String(), nullable=False, comment="Название для владельца"),
        sa.Column(
            "description",
            sa.String(),
            nullable=False,
            comment="Определение, по которому модель и человек отличают значение от соседних",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="active — используется; proposed — предложено моделью, ждёт решения; deprecated — заменено",
        ),
        sa.Column(
            "replaced_by",
            sa.String(),
            nullable=True,
            comment="Ключ значения-преемника для deprecated",
        ),
        sa.Column(
            "since_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="С какой версии таксономии значение действует",
        ),
        sa.Column(
            "created_by",
            sa.String(),
            server_default=sa.text("'seed'"),
            nullable=False,
            comment="Кто добавил: seed, model, owner",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.CheckConstraint(
            "created_by IN ('seed', 'model', 'owner')",
            name=op.f("ck_taxonomy_terms_created_by_allowed"),
        ),
        sa.CheckConstraint(
            "facet IN ('format', 'archetype', 'hook_type', 'structure', 'device', 'closer', 'tone', 'goal', 'niche')",
            name=op.f("ck_taxonomy_terms_facet_allowed"),
        ),
        sa.CheckConstraint(
            "key ~ '^[a-z][a-z0-9_]{1,39}$'", name=op.f("ck_taxonomy_terms_key_format")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'proposed', 'deprecated')",
            name=op.f("ck_taxonomy_terms_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_taxonomy_terms_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_taxonomy_terms")),
        sa.UniqueConstraint(
            "account_id", "facet", "key", name=op.f("uq_taxonomy_terms_account_id_facet_key")
        ),
        comment="Словари разбора: форматы, жанры, типы хуков, структуры, приёмы, концовки, тон, цели, ниши. Общие значения принадлежат системному аккаунту global, свои — аккаунту",
    )
    op.create_index(
        "ix_taxonomy_active",
        "taxonomy_terms",
        ["account_id", "facet"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "knowledge_embeddings",
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("model_id", sa.Uuid(), nullable=False, comment="Модель эмбеддингов"),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            comment="Версия текста знания, по которой посчитан вектор",
        ),
        sa.Column(
            "vec",
            pgvector.sqlalchemy.vector.VECTOR(dim=384),
            nullable=False,
            comment="Вектор, 384 измерения",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_embeddings_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_knowledge_embeddings_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["embedding_models.id"],
            name=op.f("fk_knowledge_embeddings_model_id_embedding_models"),
        ),
        sa.PrimaryKeyConstraint("item_id", "model_id", name=op.f("pk_knowledge_embeddings")),
        comment="Вектор текста знания (statement + guidance) для поиска похожих и подбора под задачу",
    )
    op.create_index(
        "ix_knowledge_embeddings_account",
        "knowledge_embeddings",
        ["account_id", "item_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_embeddings_hnsw",
        "knowledge_embeddings",
        ["vec"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"vec": "vector_cosine_ops"},
    )
    op.create_table(
        "knowledge_item_scopes",
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column(
            "facet",
            sa.String(),
            nullable=False,
            comment="Измерение: format, goal, niche, lang, archetype",
        ),
        sa.Column(
            "value", sa.String(), nullable=False, comment="Значение измерения (ключ таксономии)"
        ),
        sa.CheckConstraint(
            "facet IN ('format', 'goal', 'niche', 'lang', 'archetype')",
            name=op.f("ck_knowledge_item_scopes_facet_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_item_scopes_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_knowledge_item_scopes_account_id_accounts"),
        ),
        sa.PrimaryKeyConstraint("item_id", "facet", "value", name=op.f("pk_knowledge_item_scopes")),
        comment="Где знание применимо. Одно знание — много строк; нет строк по измерению — применимо ко всем его значениям",
    )
    op.create_index(
        "ix_knowledge_scopes_lookup",
        "knowledge_item_scopes",
        ["account_id", "facet", "value", "item_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_item_versions",
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("version", sa.Integer(), nullable=False, comment="Номер версии"),
        sa.Column("statement", sa.String(), nullable=False, comment="Утверждение в этой версии"),
        sa.Column("guidance", sa.String(), nullable=False, comment="Указание в этой версии"),
        sa.Column("skeleton", sa.String(), nullable=True, comment="Шаблон в этой версии"),
        sa.Column(
            "detector",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Детектор в этой версии",
        ),
        sa.Column("kind", sa.String(), nullable=False, comment="Тип знания в этой версии"),
        sa.Column("layer", sa.String(), nullable=False, comment="Слой в этой версии"),
        sa.Column("element", sa.String(), nullable=False, comment="Элемент в этой версии"),
        sa.Column("target_metric", sa.String(), nullable=False, comment="Цель в этой версии"),
        sa.Column(
            "changed_by", sa.String(), nullable=False, comment="Кто изменил: system или owner"
        ),
        sa.Column(
            "user_id", sa.Uuid(), nullable=True, comment="Пользователь, если изменил владелец"
        ),
        sa.Column(
            "reason",
            sa.String(),
            nullable=False,
            comment="Причина: extracted, merged, owner_edit, rollback",
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Происхождение: промпт, вызов модели, id материалов-входов",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.CheckConstraint(
            "changed_by IN ('system', 'owner')",
            name=op.f("ck_knowledge_item_versions_changed_by_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_item_versions_item",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_knowledge_item_versions_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_knowledge_item_versions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("item_id", "version", name=op.f("pk_knowledge_item_versions")),
        comment="Все версии формулировки знания. Правка создаёт новую версию, старая не меняется",
    )
    op.create_index(
        "ix_knowledge_item_versions_account",
        "knowledge_item_versions",
        ["account_id", "item_id"],
        unique=False,
    )
    op.create_table(
        "knowledge_links",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("from_item_id", sa.Uuid(), nullable=False, comment="Знание-источник связи"),
        sa.Column("to_item_id", sa.Uuid(), nullable=False, comment="Знание-цель связи"),
        sa.Column(
            "relation",
            sa.String(),
            nullable=False,
            comment="Тип связи: duplicates, refines, generalizes, contradicts, related",
        ),
        sa.Column("confidence", sa.Numeric(), nullable=False, comment="Уверенность судьи в связи"),
        sa.Column(
            "decided_by",
            sa.String(),
            nullable=False,
            comment="Кто установил связь: judge, owner, system",
        ),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'open'"),
            nullable=False,
            comment="open — требует решения; resolved — решено",
        ),
        sa.Column(
            "resolution",
            sa.String(),
            nullable=True,
            comment="Как решено: kept_from, kept_to, split_scope, both",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True, comment="Когда решено"),
        sa.CheckConstraint(
            "decided_by IN ('judge', 'owner', 'system')",
            name=op.f("ck_knowledge_links_decided_by_allowed"),
        ),
        sa.CheckConstraint(
            "relation IN ('duplicates', 'refines', 'generalizes', 'contradicts', 'related')",
            name=op.f("ck_knowledge_links_relation_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')", name=op.f("ck_knowledge_links_status_allowed")
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_knowledge_links_confidence_range")
        ),
        sa.CheckConstraint("from_item_id <> to_item_id", name=op.f("ck_knowledge_links_not_self")),
        sa.ForeignKeyConstraint(
            ["account_id", "from_item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_links_from_item",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "to_item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_links_to_item",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_knowledge_links_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_links")),
        sa.UniqueConstraint(
            "from_item_id",
            "to_item_id",
            "relation",
            name=op.f("uq_knowledge_links_from_item_id_to_item_id_relation"),
        ),
        comment="Связи знаний: дубль, уточнение, обобщение, противоречие. Открытое противоречие убирает обе стороны из пакета до решения",
    )
    op.create_index(
        "ix_knowledge_links_open",
        "knowledge_links",
        ["account_id", "relation"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_knowledge_links_to", "knowledge_links", ["account_id", "to_item_id"], unique=False
    )
    op.create_table(
        "knowledge_transitions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда",
        ),
        sa.Column(
            "from_status", sa.String(), nullable=True, comment="Прежний статус (NULL при создании)"
        ),
        sa.Column("to_status", sa.String(), nullable=False, comment="Новый статус"),
        sa.Column(
            "actor",
            sa.String(),
            nullable=False,
            comment="system — автоматически; owner — решение владельца",
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True, comment="Пользователь, если решил владелец"),
        sa.Column(
            "rule",
            sa.String(),
            nullable=False,
            comment="Правило перехода: auto_probation, promote_by_reviews, promote_by_own_evidence, weaken_low_quality, archive_stale, conflict_opened, owner_confirm, owner_reject, rollback",
        ),
        sa.Column("reason", sa.String(), nullable=True, comment="Пояснение для владельца"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Числа на момент перехода: веса, счётчики, пороги",
        ),
        sa.Column(
            "reverted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда переход откатили",
        ),
        sa.Column("reverted_by", sa.Uuid(), nullable=True, comment="Кто откатил"),
        sa.CheckConstraint(
            "actor IN ('system', 'owner')", name=op.f("ck_knowledge_transitions_actor_allowed")
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_transitions_item",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_knowledge_transitions_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["reverted_by"], ["users.id"], name=op.f("fk_knowledge_transitions_reverted_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_knowledge_transitions_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_transitions")),
        comment="Каждый переход статуса знания: кто, по какому правилу, с какими числами. Основа отката и недельной сводки",
    )
    op.create_index(
        "ix_knowledge_transitions_auto",
        "knowledge_transitions",
        ["account_id", sa.literal_column("ts DESC")],
        unique=False,
        postgresql_where=sa.text("actor = 'system'"),
    )
    op.create_index(
        "ix_knowledge_transitions_item",
        "knowledge_transitions",
        ["account_id", "item_id", sa.literal_column("ts DESC")],
        unique=False,
    )
    op.create_table(
        "material_analyses",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("source_id", sa.Uuid(), nullable=False, comment="Материал"),
        sa.Column(
            "analyzer_version", sa.String(), nullable=False, comment="Ключ и версия промпта разбора"
        ),
        sa.Column(
            "taxonomy_version",
            sa.Integer(),
            nullable=False,
            comment="Версия таксономии, в которой сделан разбор",
        ),
        sa.Column("model", sa.String(), nullable=False, comment="Модель, сделавшая разбор"),
        sa.Column("llm_call_id", sa.Uuid(), nullable=True, comment="Вызов в журнале llm_calls"),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Действующий разбор материала",
        ),
        sa.Column(
            "format",
            sa.String(),
            nullable=False,
            comment="Формат: post, thread, reply, quote, article",
        ),
        sa.Column("archetype", sa.String(), nullable=True, comment="Жанр (таксономия archetype)"),
        sa.Column("lang", sa.String(), nullable=True, comment="Язык текста"),
        sa.Column(
            "hook_type", sa.String(), nullable=True, comment="Тип хука (таксономия hook_type)"
        ),
        sa.Column(
            "hook_text",
            sa.String(),
            nullable=True,
            comment="Первая строка или фраза-хук — дословная цитата из материала, проверяется кодом",
        ),
        sa.Column(
            "structure", sa.String(), nullable=True, comment="Структура (таксономия structure)"
        ),
        sa.Column(
            "structure_outline",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Шаги структуры: [{step, role, quote}]",
        ),
        sa.Column(
            "tone",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Тон (таксономия tone), до трёх значений",
        ),
        sa.Column(
            "devices",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Приёмы удержания (таксономия device)",
        ),
        sa.Column("closer", sa.String(), nullable=True, comment="Тип концовки (таксономия closer)"),
        sa.Column(
            "human_markers",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Что делает текст живым: дословные фрагменты",
        ),
        sa.Column(
            "ai_markers",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Что звучит как AI: дословные фрагменты",
        ),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Признаки, посчитанные кодом: длина, число строк, цифра в первой строке, вопрос, эмодзи, ссылка, медиа, первое лицо и т. д.",
        ),
        sa.Column(
            "why_hypotheses",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Гипотезы «почему зашло или не зашло»: [{claim, quote}]. Не доказательство",
        ),
        sa.Column(
            "declared_claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Утверждения о ремесле, которые материал заявляет сам (для статей и разборов)",
        ),
        sa.Column(
            "proposed_terms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="Предложенные новые значения таксономии",
        ),
        sa.Column(
            "quality_flags",
            sa.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Флаги качества: truncated, low_content, non_text, parent_missing",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.CheckConstraint(
            "format IN ('post', 'thread', 'reply', 'quote', 'article')",
            name=op.f("ck_material_analyses_format_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "source_id"],
            ["sources.account_id", "sources.id"],
            name="fk_material_analyses_source",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_material_analyses_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_analyses")),
        comment="Разбор материала по полочкам. Производное: при новой версии промпта или таксономии создаётся новая строка, старая получает is_current = false",
    )
    op.create_index(
        "ix_material_analyses_devices",
        "material_analyses",
        ["devices"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_material_analyses_facets",
        "material_analyses",
        ["account_id", "format", "hook_type", "structure"],
        unique=False,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_material_analyses_features",
        "material_analyses",
        ["features"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"features": "jsonb_path_ops"},
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "uq_material_analyses_current",
        "material_analyses",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "material_outcomes",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("source_id", sa.Uuid(), nullable=False, comment="Материал"),
        sa.Column(
            "method_version",
            sa.Integer(),
            nullable=False,
            comment="Версия метода расчёта (пороги, формулы)",
        ),
        sa.Column(
            "basis",
            sa.String(),
            nullable=False,
            comment="На чём основан исход: own_baseline — перцентиль среди своих постов; rates — доли от просмотров; owner_label — кнопка; owner_pick — только факт отбора; none — данных нет",
        ),
        sa.Column(
            "signal_origin",
            sa.String(),
            nullable=False,
            comment="Источник использованного сигнала (см. material_signals.origin)",
        ),
        sa.Column(
            "reliability",
            sa.Numeric(precision=3, scale=2),
            nullable=False,
            comment="Надёжность исхода 0..1. Становится множителем w_source в доказательствах",
        ),
        sa.Column(
            "engagement_rate",
            sa.Numeric(),
            nullable=True,
            comment="(лайки + репосты + цитаты) / просмотры",
        ),
        sa.Column("reply_rate", sa.Numeric(), nullable=True, comment="ответы / просмотры"),
        sa.Column("save_rate", sa.Numeric(), nullable=True, comment="закладки / просмотры"),
        sa.Column(
            "reach_ratio", sa.Numeric(), nullable=True, comment="просмотры / подписчики автора"
        ),
        sa.Column(
            "pct_reach",
            sa.Numeric(),
            nullable=True,
            comment="Перцентиль охвата среди своих постов того же формата за 90 дней (только для своих)",
        ),
        sa.Column(
            "pct_engagement",
            sa.Numeric(),
            nullable=True,
            comment="Перцентиль вовлечённости среди своих постов (только для своих)",
        ),
        sa.Column(
            "pct_replies",
            sa.Numeric(),
            nullable=True,
            comment="Перцентиль доли ответов среди своих постов (только для своих)",
        ),
        sa.Column(
            "pct_saves",
            sa.Numeric(),
            nullable=True,
            comment="Перцентиль доли закладок среди своих постов (только для своих)",
        ),
        sa.Column(
            "tier_reach",
            sa.String(),
            server_default=sa.text("'unknown'"),
            nullable=False,
            comment="Исход по цели «охват»: hit, above, normal, below, flop, unknown",
        ),
        sa.Column(
            "tier_engagement",
            sa.String(),
            server_default=sa.text("'unknown'"),
            nullable=False,
            comment="Исход по цели «вовлечённость»",
        ),
        sa.Column(
            "tier_replies",
            sa.String(),
            server_default=sa.text("'unknown'"),
            nullable=False,
            comment="Исход по цели «ответы»",
        ),
        sa.Column(
            "tier_saves",
            sa.String(),
            server_default=sa.text("'unknown'"),
            nullable=False,
            comment="Исход по цели «сохранения»",
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="Действующая версия исхода",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда посчитано",
        ),
        sa.CheckConstraint(
            "basis IN ('own_baseline', 'rates', 'owner_label', 'owner_pick', 'none')",
            name=op.f("ck_material_outcomes_basis_allowed"),
        ),
        sa.CheckConstraint(
            "tier_engagement IN ('hit', 'above', 'normal', 'below', 'flop', 'unknown')",
            name=op.f("ck_material_outcomes_tier_engagement_allowed"),
        ),
        sa.CheckConstraint(
            "tier_reach IN ('hit', 'above', 'normal', 'below', 'flop', 'unknown')",
            name=op.f("ck_material_outcomes_tier_reach_allowed"),
        ),
        sa.CheckConstraint(
            "tier_replies IN ('hit', 'above', 'normal', 'below', 'flop', 'unknown')",
            name=op.f("ck_material_outcomes_tier_replies_allowed"),
        ),
        sa.CheckConstraint(
            "tier_saves IN ('hit', 'above', 'normal', 'below', 'flop', 'unknown')",
            name=op.f("ck_material_outcomes_tier_saves_allowed"),
        ),
        sa.CheckConstraint(
            "pct_engagement BETWEEN 0 AND 1", name=op.f("ck_material_outcomes_pct_engagement_range")
        ),
        sa.CheckConstraint(
            "pct_reach BETWEEN 0 AND 1", name=op.f("ck_material_outcomes_pct_reach_range")
        ),
        sa.CheckConstraint(
            "pct_replies BETWEEN 0 AND 1", name=op.f("ck_material_outcomes_pct_replies_range")
        ),
        sa.CheckConstraint(
            "pct_saves BETWEEN 0 AND 1", name=op.f("ck_material_outcomes_pct_saves_range")
        ),
        sa.CheckConstraint(
            "reliability BETWEEN 0 AND 1", name=op.f("ck_material_outcomes_reliability_range")
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "source_id"],
            ["sources.account_id", "sources.id"],
            name="fk_material_outcomes_source",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_material_outcomes_account_id_accounts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_outcomes")),
        comment="Исход материала по четырём целям. Считается кодом из сигналов, при смене метода пересчитывается; старые версии остаются с is_current = false",
    )
    op.create_index(
        "ix_material_outcomes_account",
        "material_outcomes",
        ["account_id", sa.literal_column("computed_at DESC")],
        unique=False,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "uq_material_outcomes_current",
        "material_outcomes",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "material_signals",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("source_id", sa.Uuid(), nullable=False, comment="Материал"),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда сняты цифры",
        ),
        sa.Column(
            "origin",
            sa.String(),
            nullable=False,
            comment="Источник: x_api, screenshot, manual_numbers, owner_label (кнопка), owner_pick (сам факт отбора), own_sync (свой пост из этапа 14)",
        ),
        sa.Column(
            "post_age_hours",
            sa.Numeric(),
            nullable=True,
            comment="Возраст поста на момент наблюдения. Моложе 24 часов — исход не считается",
        ),
        sa.Column("views", sa.BigInteger(), nullable=True, comment="Просмотры"),
        sa.Column("likes", sa.Integer(), nullable=True, comment="Лайки"),
        sa.Column("replies", sa.Integer(), nullable=True, comment="Ответы"),
        sa.Column("reposts", sa.Integer(), nullable=True, comment="Репосты"),
        sa.Column("quotes", sa.Integer(), nullable=True, comment="Цитаты"),
        sa.Column("bookmarks", sa.Integer(), nullable=True, comment="Закладки"),
        sa.Column(
            "author_followers",
            sa.Integer(),
            nullable=True,
            comment="Подписчики автора на момент наблюдения",
        ),
        sa.Column(
            "owner_label", sa.String(), nullable=True, comment="Оценка владельца: hit, normal, flop"
        ),
        sa.Column(
            "read_confidence",
            sa.Numeric(precision=3, scale=2),
            server_default=sa.text("1"),
            nullable=False,
            comment="Уверенность чтения цифр (для скриншота — от модели, после подтверждения владельцем = 1)",
        ),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True, comment="Кто подтвердил цифры"),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="Сырой ответ источника",
        ),
        sa.CheckConstraint(
            "(origin = 'owner_label') = (owner_label IS NOT NULL)",
            name=op.f("ck_material_signals_owner_label_origin"),
        ),
        sa.CheckConstraint(
            "origin IN ('x_api', 'screenshot', 'manual_numbers', 'owner_label', 'owner_pick', 'own_sync')",
            name=op.f("ck_material_signals_origin_allowed"),
        ),
        sa.CheckConstraint(
            "owner_label IN ('hit', 'normal', 'flop')",
            name=op.f("ck_material_signals_owner_label_allowed"),
        ),
        sa.CheckConstraint(
            "author_followers >= 0", name=op.f("ck_material_signals_author_followers_nonneg")
        ),
        sa.CheckConstraint("bookmarks >= 0", name=op.f("ck_material_signals_bookmarks_nonneg")),
        sa.CheckConstraint("likes >= 0", name=op.f("ck_material_signals_likes_nonneg")),
        sa.CheckConstraint(
            "post_age_hours >= 0", name=op.f("ck_material_signals_post_age_hours_nonneg")
        ),
        sa.CheckConstraint("quotes >= 0", name=op.f("ck_material_signals_quotes_nonneg")),
        sa.CheckConstraint(
            "read_confidence BETWEEN 0 AND 1",
            name=op.f("ck_material_signals_read_confidence_range"),
        ),
        sa.CheckConstraint("replies >= 0", name=op.f("ck_material_signals_replies_nonneg")),
        sa.CheckConstraint("reposts >= 0", name=op.f("ck_material_signals_reposts_nonneg")),
        sa.CheckConstraint("views >= 0", name=op.f("ck_material_signals_views_nonneg")),
        sa.ForeignKeyConstraint(
            ["account_id", "source_id"],
            ["sources.account_id", "sources.id"],
            name="fk_material_signals_source",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_material_signals_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name=op.f("fk_material_signals_confirmed_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_material_signals")),
        comment="Наблюдения результата материала: цифры из API, со скриншота, вручную или оценка владельца. Несколько наблюдений на материал, строки не меняются",
    )
    op.create_index(
        "ix_material_signals_latest",
        "material_signals",
        ["account_id", "source_id", sa.literal_column("observed_at DESC")],
        unique=False,
    )
    op.create_table(
        "knowledge_applications",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("pack_id", sa.Uuid(), nullable=False, comment="Пакет, из которого пришло знание"),
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column("draft_id", sa.Uuid(), nullable=False, comment="Черновик"),
        sa.Column(
            "slot",
            sa.String(),
            nullable=False,
            comment="Роль в пакете: core, probation (испытание), anti (чего избегать), pinned",
        ),
        sa.Column(
            "applied",
            sa.Boolean(),
            nullable=True,
            comment="Применено ли знание в тексте: true, false, NULL — не проверялось",
        ),
        sa.Column(
            "applied_score", sa.Numeric(), nullable=True, comment="Уверенность детектора, 0..1"
        ),
        sa.Column(
            "checked_by",
            sa.String(),
            nullable=True,
            comment="Чем проверено: feature (код) или rubric (судья)",
        ),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), nullable=True, comment="Когда проверено"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Создано",
        ),
        sa.CheckConstraint(
            "checked_by IN ('feature', 'rubric')",
            name=op.f("ck_knowledge_applications_checked_by_allowed"),
        ),
        sa.CheckConstraint(
            "slot IN ('core', 'probation', 'anti', 'pinned')",
            name=op.f("ck_knowledge_applications_slot_allowed"),
        ),
        sa.CheckConstraint(
            "applied_score BETWEEN 0 AND 1",
            name=op.f("ck_knowledge_applications_applied_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_applications_item",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "pack_id"],
            ["knowledge_packs.account_id", "knowledge_packs.id"],
            name="fk_knowledge_applications_pack",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_knowledge_applications_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name=op.f("fk_knowledge_applications_draft_id_drafts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_applications")),
        sa.UniqueConstraint(
            "draft_id", "item_id", name=op.f("uq_knowledge_applications_draft_id_item_id")
        ),
        comment="Какие знания были даны автору для черновика и какие реально применены в тексте. Связывает знание с решением владельца и с исходом поста",
    )
    op.create_index(
        "ix_knowledge_applications_item",
        "knowledge_applications",
        ["account_id", "item_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "knowledge_evidence",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False, comment="UUIDv7"
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False, comment="Тенант"),
        sa.Column("item_id", sa.Uuid(), nullable=False, comment="Знание"),
        sa.Column(
            "source_id",
            sa.Uuid(),
            nullable=True,
            comment="Материал-доказательство (свой или чужой пост)",
        ),
        sa.Column(
            "draft_id",
            sa.Uuid(),
            nullable=True,
            comment="Черновик, если доказательство — решение владельца по черновику",
        ),
        sa.Column(
            "dimension",
            sa.String(),
            nullable=False,
            comment="eff — результативность (исходы постов); fit — совместимость с владельцем (его решения)",
        ),
        sa.Column(
            "basis",
            sa.String(),
            nullable=False,
            comment="Основание: observed_outcome — исход чужого поста; own_outcome — исход своего; declared — заявлено в статье; owner_pick — владелец отобрал; review — решение по черновику; owner_decision — подтверждение знания",
        ),
        sa.Column(
            "polarity",
            sa.SmallInteger(),
            nullable=False,
            comment="+1 подтверждает, −1 опровергает, 0 — знание встретилось, исход нейтрален",
        ),
        sa.Column(
            "detector_score",
            sa.Numeric(),
            nullable=True,
            comment="Насколько уверенно знание распознано в тексте, 0..1",
        ),
        sa.Column(
            "outcome_tier",
            sa.String(),
            nullable=True,
            comment="Исход материала по цели знания на момент записи",
        ),
        sa.Column(
            "target_metric", sa.String(), nullable=True, comment="Цель, по которой брался исход"
        ),
        sa.Column(
            "format",
            sa.String(),
            nullable=True,
            comment="Формат материала — для разреза весов по форматам",
        ),
        sa.Column(
            "author_id",
            sa.Uuid(),
            nullable=True,
            comment="Автор материала — для счёта независимых авторов",
        ),
        sa.Column("w_source", sa.Numeric(), nullable=False, comment="Надёжность источника исхода"),
        sa.Column(
            "w_scope",
            sa.Numeric(),
            nullable=False,
            comment="Близость: свой пост 2.0, своя ниша 1.0, ниша неизвестна 0.8, чужая ниша 0.5",
        ),
        sa.Column(
            "w_magnitude",
            sa.Numeric(),
            nullable=False,
            comment="Сила исхода: hit и flop 1.0, above и below 0.5, normal 0",
        ),
        sa.Column(
            "w_author",
            sa.Numeric(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Скидка за повтор автора: 1/k для k-го доказательства от того же автора",
        ),
        sa.Column(
            "weight",
            sa.Numeric(),
            sa.Computed("w_source * w_scope * w_magnitude * w_author", persisted=True),
            nullable=False,
            comment="Итоговый вес без учёта свежести (свежесть применяется при пересчёте)",
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Дата события, от которой считается свежесть: выход поста или решение владельца",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Когда доказательство записано",
        ),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), nullable=True, comment="Когда отозвано"
        ),
        sa.Column(
            "revoked_reason",
            sa.String(),
            nullable=True,
            comment="Почему отозвано: source_removed, author_blocked, rollback, reanalysis, owner",
        ),
        sa.CheckConstraint(
            "basis IN ('observed_outcome', 'own_outcome', 'declared', 'owner_pick', 'review', 'owner_decision')",
            name=op.f("ck_knowledge_evidence_basis_allowed"),
        ),
        sa.CheckConstraint(
            "basis NOT IN ('review', 'owner_decision') OR dimension = 'fit'",
            name=op.f("ck_knowledge_evidence_decisions_are_fit"),
        ),
        sa.CheckConstraint(
            "dimension IN ('eff', 'fit')", name=op.f("ck_knowledge_evidence_dimension_allowed")
        ),
        sa.CheckConstraint(
            "source_id IS NOT NULL OR draft_id IS NOT NULL OR basis = 'owner_decision'",
            name=op.f("ck_knowledge_evidence_has_subject"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name=op.f("ck_knowledge_evidence_revoked_reason"),
        ),
        sa.CheckConstraint(
            "detector_score BETWEEN 0 AND 1",
            name=op.f("ck_knowledge_evidence_detector_score_range"),
        ),
        sa.CheckConstraint(
            "polarity IN (-1, 0, 1)", name=op.f("ck_knowledge_evidence_polarity_allowed")
        ),
        sa.CheckConstraint(
            "w_author BETWEEN 0 AND 1", name=op.f("ck_knowledge_evidence_w_author_range")
        ),
        sa.CheckConstraint(
            "w_magnitude BETWEEN 0 AND 1", name=op.f("ck_knowledge_evidence_w_magnitude_range")
        ),
        sa.CheckConstraint(
            "w_scope BETWEEN 0 AND 2", name=op.f("ck_knowledge_evidence_w_scope_range")
        ),
        sa.CheckConstraint(
            "w_source BETWEEN 0 AND 3", name=op.f("ck_knowledge_evidence_w_source_range")
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "author_id"],
            ["authors.account_id", "authors.id"],
            name="fk_knowledge_evidence_author",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "item_id"],
            ["knowledge_items.account_id", "knowledge_items.id"],
            name="fk_knowledge_evidence_item",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "source_id"],
            ["sources.account_id", "sources.id"],
            name="fk_knowledge_evidence_source",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_knowledge_evidence_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["drafts.id"], name=op.f("fk_knowledge_evidence_draft_id_drafts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_evidence")),
        comment="Доказательства за и против знания. Строки не меняются и не удаляются: ошибочное доказательство отзывается (revoked_at). Из этой таблицы веса пересчитываются на любую дату",
    )
    op.create_index(
        "ix_evidence_item",
        "knowledge_evidence",
        ["account_id", "item_id", "dimension"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_evidence_source",
        "knowledge_evidence",
        ["account_id", "source_id"],
        unique=False,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_index(
        "ix_evidence_time", "knowledge_evidence", ["account_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_evidence_draft",
        "knowledge_evidence",
        ["item_id", "draft_id", "basis", "dimension"],
        unique=True,
        postgresql_where=sa.text(
            "revoked_at IS NULL AND draft_id IS NOT NULL AND source_id IS NULL"
        ),
    )
    op.create_index(
        "uq_evidence_source",
        "knowledge_evidence",
        ["item_id", "source_id", "basis", "dimension"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND source_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_lessons_account_id_status"), table_name="lessons")
    op.drop_table("lessons")
    op.add_column(
        "drafts",
        sa.Column(
            "probation_item_id",
            sa.Uuid(),
            nullable=True,
            comment="Знание на испытании, которое проверяется в этом варианте (пометка 🧪). NULL — обычный вариант",
        ),
    )
    op.create_foreign_key(
        op.f("fk_drafts_probation_item_id_knowledge_items"),
        "drafts",
        "knowledge_items",
        ["probation_item_id"],
        ["id"],
    )
    op.add_column(
        "embedding_models",
        sa.Column(
            "dup_threshold",
            sa.Numeric(),
            server_default=sa.text("0.88"),
            nullable=False,
            comment="Порог косинусного сходства «это одно и то же знание» для этой модели",
        ),
    )
    op.add_column(
        "embedding_models",
        sa.Column(
            "related_threshold",
            sa.Numeric(),
            server_default=sa.text("0.70"),
            nullable=False,
            comment="Порог сходства «знания связаны, проверить судьёй» для этой модели",
        ),
    )
    op.add_column(
        "generation_runs",
        sa.Column(
            "knowledge_pack_id",
            sa.Uuid(),
            nullable=True,
            comment="Пакет знаний, с которым писался этот запуск",
        ),
    )
    op.create_foreign_key(
        op.f("fk_generation_runs_knowledge_pack_id_knowledge_packs"),
        "generation_runs",
        "knowledge_packs",
        ["knowledge_pack_id"],
        ["id"],
    )
    _run(EMBEDDING_CHECKS_UP)
    _run(SNAPSHOTS_DEFAULT_PARTITION)
    for sql in BRAIN_SQL:
        _run(sql)


def downgrade() -> None:
    for sql in BRAIN_SQL_DOWN:
        op.execute(sql)
    for name, _ in SOURCES_CHECKS_NEW:
        op.drop_constraint(op.f(name), "sources", type_="check")
    _run(SOURCES_DOWN)
    op.drop_constraint(
        op.f("fk_generation_runs_knowledge_pack_id_knowledge_packs"),
        "generation_runs",
        type_="foreignkey",
    )
    op.drop_column("generation_runs", "knowledge_pack_id")
    op.drop_column("embedding_models", "related_threshold")
    op.drop_column("embedding_models", "dup_threshold")
    op.drop_constraint(
        op.f("fk_drafts_probation_item_id_knowledge_items"), "drafts", type_="foreignkey"
    )
    op.drop_column("drafts", "probation_item_id")
    op.create_table(
        "lessons",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("uuidv7()"),
            autoincrement=False,
            nullable=False,
            comment="Идентификатор (UUIDv7, упорядочен по времени)",
        ),
        sa.Column(
            "account_id",
            sa.UUID(),
            autoincrement=False,
            nullable=False,
            comment="Аккаунт-владелец (тенант)",
        ),
        sa.Column(
            "kind",
            sa.VARCHAR(),
            autoincrement=False,
            nullable=False,
            comment="do — делать, dont — не делать, pattern — приём",
        ),
        sa.Column(
            "text", sa.VARCHAR(), autoincrement=False, nullable=False, comment="Формулировка урока"
        ),
        sa.Column(
            "status",
            sa.VARCHAR(),
            server_default=sa.text("'proposed'::character varying"),
            autoincrement=False,
            nullable=False,
            comment="proposed / accepted / rejected / retired",
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            autoincrement=False,
            nullable=False,
            comment="ID ревью, из которых выведен урок",
        ),
        sa.Column(
            "weight",
            sa.DOUBLE_PRECISION(precision=53),
            server_default=sa.text("1.0"),
            autoincrement=False,
            nullable=False,
            comment="Вес урока при сборке инструкций",
        ),
        sa.Column(
            "decided_by",
            sa.UUID(),
            autoincrement=False,
            nullable=True,
            comment="Кто принял решение по уроку",
        ),
        sa.Column(
            "decided_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
            comment="Когда принято решение (UTC)",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
            comment="Когда создано (UTC)",
        ),
        sa.CheckConstraint(
            "kind::text = ANY (ARRAY['do'::character varying, 'dont'::character varying, 'pattern'::character varying]::text[])",
            name=op.f("ck_lessons_kind_allowed"),
        ),
        sa.CheckConstraint(
            "status::text = ANY (ARRAY['proposed'::character varying, 'accepted'::character varying, 'rejected'::character varying, 'retired'::character varying]::text[])",
            name=op.f("ck_lessons_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_lessons_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_lessons_decided_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
        comment="Правила из правок и отказов владельца",
    )
    op.create_index(
        op.f("ix_lessons_account_id_status"), "lessons", ["account_id", "status"], unique=False
    )
    op.drop_index(
        "uq_evidence_source",
        table_name="knowledge_evidence",
        postgresql_where=sa.text("revoked_at IS NULL AND source_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_evidence_draft",
        table_name="knowledge_evidence",
        postgresql_where=sa.text(
            "revoked_at IS NULL AND draft_id IS NOT NULL AND source_id IS NULL"
        ),
    )
    op.drop_index("ix_evidence_time", table_name="knowledge_evidence")
    op.drop_index(
        "ix_evidence_source",
        table_name="knowledge_evidence",
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_evidence_item",
        table_name="knowledge_evidence",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("knowledge_evidence")
    op.drop_index("ix_knowledge_applications_item", table_name="knowledge_applications")
    op.drop_table("knowledge_applications")
    op.drop_index("ix_material_signals_latest", table_name="material_signals")
    op.drop_table("material_signals")
    op.drop_index(
        "uq_material_outcomes_current",
        table_name="material_outcomes",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index(
        "ix_material_outcomes_account",
        table_name="material_outcomes",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_table("material_outcomes")
    op.drop_index(
        "uq_material_analyses_current",
        table_name="material_analyses",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index(
        "ix_material_analyses_features",
        table_name="material_analyses",
        postgresql_using="gin",
        postgresql_ops={"features": "jsonb_path_ops"},
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index(
        "ix_material_analyses_facets",
        table_name="material_analyses",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index(
        "ix_material_analyses_devices",
        table_name="material_analyses",
        postgresql_using="gin",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_table("material_analyses")
    op.drop_index("ix_knowledge_transitions_item", table_name="knowledge_transitions")
    op.drop_index(
        "ix_knowledge_transitions_auto",
        table_name="knowledge_transitions",
        postgresql_where=sa.text("actor = 'system'"),
    )
    op.drop_table("knowledge_transitions")
    op.drop_index("ix_knowledge_links_to", table_name="knowledge_links")
    op.drop_index(
        "ix_knowledge_links_open",
        table_name="knowledge_links",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_table("knowledge_links")
    op.drop_index("ix_knowledge_item_versions_account", table_name="knowledge_item_versions")
    op.drop_table("knowledge_item_versions")
    op.drop_index("ix_knowledge_scopes_lookup", table_name="knowledge_item_scopes")
    op.drop_table("knowledge_item_scopes")
    op.drop_index(
        "ix_knowledge_embeddings_hnsw",
        table_name="knowledge_embeddings",
        postgresql_using="hnsw",
        postgresql_ops={"vec": "vector_cosine_ops"},
    )
    op.drop_index("ix_knowledge_embeddings_account", table_name="knowledge_embeddings")
    op.drop_table("knowledge_embeddings")
    op.drop_index(
        "ix_taxonomy_active",
        table_name="taxonomy_terms",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("taxonomy_terms")
    op.drop_table("segments")
    op.drop_index("ix_knowledge_snapshots_item", table_name="knowledge_snapshots")
    op.drop_table("knowledge_snapshots")
    op.drop_index("ix_knowledge_packs_time", table_name="knowledge_packs")
    op.drop_table("knowledge_packs")
    op.drop_index(
        "ix_knowledge_items_trgm",
        table_name="knowledge_items",
        postgresql_using="gin",
        postgresql_ops={"statement": "gin_trgm_ops"},
    )
    op.drop_index("ix_knowledge_items_status", table_name="knowledge_items")
    op.drop_index(
        "ix_knowledge_items_parent",
        table_name="knowledge_items",
        postgresql_where=sa.text("parent_item_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_knowledge_items_pack",
        table_name="knowledge_items",
        postgresql_where=sa.text("status IN ('active', 'probation') AND owner_state <> 'disabled'"),
    )
    op.drop_table("knowledge_items")
    op.drop_constraint("fk_sources_thread", "sources", type_="foreignkey")
    op.drop_constraint("fk_sources_parent", "sources", type_="foreignkey")
    op.drop_constraint("fk_sources_author", "sources", type_="foreignkey")
    op.drop_constraint(op.f("uq_sources_account_id_id"), "sources", type_="unique")
    op.drop_index(
        "ix_sources_parent",
        table_name="sources",
        postgresql_where=sa.text("parent_source_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_sources_learning", table_name="sources", postgresql_where=sa.text("removed_at IS NULL")
    )
    op.drop_index("ix_sources_author", table_name="sources")
    op.alter_column(
        "sources",
        "origin",
        existing_type=sa.VARCHAR(),
        comment="Откуда: manual / x_api / oembed / radar",
        existing_comment="Канал получения: manual_text, link_oembed, x_api, screenshot, tg_forward, file, url_fetch, history_backfill, radar",
        existing_nullable=False,
    )
    op.alter_column(
        "sources",
        "kind",
        existing_type=sa.VARCHAR(),
        comment="Что это: own_post / liked_sample / competitor_post / file / chat / note",
        existing_comment="Что это физически: post, thread, reply, quote, repost, article, note, file, chat",
        existing_nullable=False,
    )
    op.drop_column("sources", "removed_at")
    op.drop_column("sources", "quarantine_reason")
    op.drop_column("sources", "analysis_status")
    op.drop_column("sources", "owner_note")
    op.drop_column("sources", "lang")
    op.drop_column("sources", "posted_at")
    op.drop_column("sources", "thread_root_id")
    op.drop_column("sources", "parent_source_id")
    op.drop_column("sources", "author_id")
    op.drop_column("sources", "stance")
    op.drop_index("uq_authors_self", table_name="authors", postgresql_where=sa.text("is_self"))
    op.drop_table("authors")
    for name, sql in SOURCES_CHECKS_OLD:
        op.create_check_constraint(op.f(name), "sources", sql)
