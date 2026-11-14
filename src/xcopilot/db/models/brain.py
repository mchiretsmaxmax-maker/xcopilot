"""«Мозг» (ТЗ v3.1, приложение A): разбор материалов, знания, доказательства, выдача.

Все таблицы тенантные; ссылки внутри аккаунта — составные внешние ключи `(account_id, id)`,
поэтому строка одного аккаунта физически не может сослаться на строку другого.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CHAR,
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from xcopilot.db.models.base import (
    Base,
    account_fk,
    created_at,
    jsonb,
    one_of,
    updated_at,
    uuid_pk,
)
from xcopilot.db.models.knowledge import EMBEDDING_DIM

TIERS = ("hit", "above", "normal", "below", "flop", "unknown")
SIGNAL_ORIGINS = ("x_api", "screenshot", "manual_numbers", "owner_label", "owner_pick", "own_sync")
OUTCOME_BASES = ("own_baseline", "rates", "owner_label", "owner_pick", "none")
TAXONOMY_FACETS = (
    "format",
    "archetype",
    "hook_type",
    "structure",
    "device",
    "closer",
    "tone",
    "goal",
    "niche",
)
TERM_STATUSES = ("active", "proposed", "deprecated")
TERM_AUTHORS = ("seed", "model", "owner")
ANALYSIS_FORMATS = ("post", "thread", "reply", "quote", "article")
ITEM_KINDS = ("thesis", "hook_pattern", "structure", "anti_pattern", "lexicon", "exemplar")
ITEM_LAYERS = ("voice", "humanness", "craft", "niche", "platform")
ITEM_ELEMENTS = (
    "hook",
    "structure",
    "device",
    "closer",
    "tone",
    "lexicon",
    "packaging",
    "timing",
    "general",
)
TARGET_METRICS = ("reach", "engagement", "replies", "saves", "quality")
RISK_CLASSES = ("A", "B", "C")
ITEM_STATUSES = (
    "candidate",
    "probation",
    "active",
    "weakened",
    "conflicted",
    "archived",
    "rejected",
    "merged",
)
OWNER_STATES = ("none", "confirmed", "pinned", "disabled")
ITEM_ORIGINS = ("materials", "edits", "own_metrics", "owner", "seed")
ACTORS = ("system", "owner")
SCOPE_FACETS = ("format", "goal", "niche", "lang", "archetype")
EVIDENCE_DIMENSIONS = ("eff", "fit")
EVIDENCE_BASES = (
    "observed_outcome",
    "own_outcome",
    "declared",
    "owner_pick",
    "review",
    "owner_decision",
)
LINK_RELATIONS = ("duplicates", "refines", "generalizes", "contradicts", "related")
LINK_DECIDERS = ("judge", "owner", "system")
LINK_STATUSES = ("open", "resolved")
PACK_CONSUMERS = ("writer", "export", "mcp", "eval")
APPLICATION_SLOTS = ("core", "probation", "anti", "pinned")
CHECKERS = ("feature", "rubric")
KEY_PATTERN = "key ~ '^[a-z][a-z0-9_]{1,39}$'"


def _fk(
    cols: list[str], table: str, name: str, ondelete: str | None = None
) -> ForeignKeyConstraint:
    """Составной внешний ключ (account_id, <col>) → <table>(account_id, id)."""
    return ForeignKeyConstraint(
        ["account_id", *cols], [f"{table}.account_id", f"{table}.id"], name=name, ondelete=ondelete
    )


def _num(comment: str, **kw: Any) -> Mapped[Any]:
    return mapped_column(Numeric, comment=comment, **kw)


def _between(col: str, lo: int, hi: int) -> CheckConstraint:
    return CheckConstraint(f"{col} BETWEEN {lo} AND {hi}", name=f"{col}_range")


def _nonneg(col: str) -> CheckConstraint:
    return CheckConstraint(f"{col} >= 0", name=f"{col}_nonneg")


# --- материалы ---------------------------------------------------------------------------


class MaterialSignal(Base):
    __tablename__ = "material_signals"
    __table_args__ = (
        Index(
            "ix_material_signals_latest", "account_id", "source_id", sql_text("observed_at DESC")
        ),
        _fk(["source_id"], "sources", "fk_material_signals_source"),
        one_of("origin", SIGNAL_ORIGINS),
        one_of("owner_label", ("hit", "normal", "flop")),
        *(_nonneg(c) for c in ("post_age_hours", "views", "likes", "replies", "reposts")),
        *(_nonneg(c) for c in ("quotes", "bookmarks", "author_followers")),
        _between("read_confidence", 0, 1),
        CheckConstraint(
            "(origin = 'owner_label') = (owner_label IS NOT NULL)", name="owner_label_origin"
        ),
        {
            "comment": "Наблюдения результата материала: цифры из API, со скриншота, вручную или"
            " оценка владельца. Несколько наблюдений на материал, строки не меняются"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    source_id: Mapped[uuid.UUID] = mapped_column(comment="Материал")
    observed_at: Mapped[datetime] = created_at("Когда сняты цифры")
    origin: Mapped[str] = mapped_column(
        comment="Источник: x_api, screenshot, manual_numbers, owner_label (кнопка), owner_pick"
        " (сам факт отбора), own_sync (свой пост из этапа 14)"
    )
    post_age_hours: Mapped[Decimal | None] = _num(
        "Возраст поста на момент наблюдения. Моложе 24 часов — исход не считается"
    )
    views: Mapped[int | None] = mapped_column(BigInteger, comment="Просмотры")
    likes: Mapped[int | None] = mapped_column(comment="Лайки")
    replies: Mapped[int | None] = mapped_column(comment="Ответы")
    reposts: Mapped[int | None] = mapped_column(comment="Репосты")
    quotes: Mapped[int | None] = mapped_column(comment="Цитаты")
    bookmarks: Mapped[int | None] = mapped_column(comment="Закладки")
    author_followers: Mapped[int | None] = mapped_column(
        comment="Подписчики автора на момент наблюдения"
    )
    owner_label: Mapped[str | None] = mapped_column(comment="Оценка владельца: hit, normal, flop")
    read_confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2),
        server_default=sql_text("1"),
        comment="Уверенность чтения цифр (для скриншота — от модели, после подтверждения"
        " владельцем = 1)",
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), comment="Кто подтвердил цифры"
    )
    raw: Mapped[dict[str, Any]] = jsonb("Сырой ответ источника")


class MaterialOutcome(Base):
    __tablename__ = "material_outcomes"
    __table_args__ = (
        Index(
            "uq_material_outcomes_current",
            "source_id",
            unique=True,
            postgresql_where=sql_text("is_current"),
        ),
        Index(
            "ix_material_outcomes_account",
            "account_id",
            sql_text("computed_at DESC"),
            postgresql_where=sql_text("is_current"),
        ),
        _fk(["source_id"], "sources", "fk_material_outcomes_source"),
        one_of("basis", OUTCOME_BASES),
        _between("reliability", 0, 1),
        *(_between(f"pct_{g}", 0, 1) for g in ("reach", "engagement", "replies", "saves")),
        *(one_of(f"tier_{g}", TIERS) for g in ("reach", "engagement", "replies", "saves")),
        {
            "comment": "Исход материала по четырём целям. Считается кодом из сигналов, при смене"
            " метода пересчитывается; старые версии остаются с is_current = false"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    source_id: Mapped[uuid.UUID] = mapped_column(comment="Материал")
    method_version: Mapped[int] = mapped_column(comment="Версия метода расчёта (пороги, формулы)")
    basis: Mapped[str] = mapped_column(
        comment="На чём основан исход: own_baseline — перцентиль среди своих постов; rates —"
        " доли от просмотров; owner_label — кнопка; owner_pick — только факт отбора; none —"
        " данных нет"
    )
    signal_origin: Mapped[str] = mapped_column(
        comment="Источник использованного сигнала (см. material_signals.origin)"
    )
    reliability: Mapped[Decimal] = mapped_column(
        Numeric(3, 2),
        comment="Надёжность исхода 0..1. Становится множителем w_source в доказательствах",
    )
    engagement_rate: Mapped[Decimal | None] = _num("(лайки + репосты + цитаты) / просмотры")
    reply_rate: Mapped[Decimal | None] = _num("ответы / просмотры")
    save_rate: Mapped[Decimal | None] = _num("закладки / просмотры")
    reach_ratio: Mapped[Decimal | None] = _num("просмотры / подписчики автора")
    pct_reach: Mapped[Decimal | None] = _num(
        "Перцентиль охвата среди своих постов того же формата за 90 дней (только для своих)"
    )
    pct_engagement: Mapped[Decimal | None] = _num(
        "Перцентиль вовлечённости среди своих постов (только для своих)"
    )
    pct_replies: Mapped[Decimal | None] = _num(
        "Перцентиль доли ответов среди своих постов (только для своих)"
    )
    pct_saves: Mapped[Decimal | None] = _num(
        "Перцентиль доли закладок среди своих постов (только для своих)"
    )
    tier_reach: Mapped[str] = mapped_column(
        server_default=sql_text("'unknown'"),
        comment="Исход по цели «охват»: hit, above, normal, below, flop, unknown",
    )
    tier_engagement: Mapped[str] = mapped_column(
        server_default=sql_text("'unknown'"), comment="Исход по цели «вовлечённость»"
    )
    tier_replies: Mapped[str] = mapped_column(
        server_default=sql_text("'unknown'"), comment="Исход по цели «ответы»"
    )
    tier_saves: Mapped[str] = mapped_column(
        server_default=sql_text("'unknown'"), comment="Исход по цели «сохранения»"
    )
    is_current: Mapped[bool] = mapped_column(
        server_default=sql_text("true"), comment="Действующая версия исхода"
    )
    computed_at: Mapped[datetime] = created_at("Когда посчитано")


class TaxonomyTerm(Base):
    __tablename__ = "taxonomy_terms"
    __table_args__ = (
        UniqueConstraint("account_id", "facet", "key"),
        Index(
            "ix_taxonomy_active",
            "account_id",
            "facet",
            postgresql_where=sql_text("status = 'active'"),
        ),
        one_of("facet", TAXONOMY_FACETS),
        CheckConstraint(KEY_PATTERN, name="key_format"),
        one_of("status", TERM_STATUSES),
        one_of("created_by", TERM_AUTHORS),
        {
            "comment": "Словари разбора: форматы, жанры, типы хуков, структуры, приёмы, концовки,"
            " тон, цели, ниши. Общие значения принадлежат системному аккаунту global, свои —"
            " аккаунту"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант (global для общих значений)")
    facet: Mapped[str] = mapped_column(comment="Какой словарь")
    key: Mapped[str] = mapped_column(comment="Ключ значения, латиницей")
    title: Mapped[str] = mapped_column(comment="Название для владельца")
    description: Mapped[str] = mapped_column(
        comment="Определение, по которому модель и человек отличают значение от соседних"
    )
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'active'"),
        comment="active — используется; proposed — предложено моделью, ждёт решения;"
        " deprecated — заменено",
    )
    replaced_by: Mapped[str | None] = mapped_column(
        comment="Ключ значения-преемника для deprecated"
    )
    since_version: Mapped[int] = mapped_column(
        server_default=sql_text("1"), comment="С какой версии таксономии значение действует"
    )
    created_by: Mapped[str] = mapped_column(
        server_default=sql_text("'seed'"), comment="Кто добавил: seed, model, owner"
    )
    created_at: Mapped[datetime] = created_at("Создано")


def _text_array(comment: str) -> Mapped[list[str]]:
    return mapped_column(ARRAY(Text), server_default=sql_text("'{}'::text[]"), comment=comment)


class MaterialAnalysis(Base):
    __tablename__ = "material_analyses"
    __table_args__ = (
        Index(
            "uq_material_analyses_current",
            "source_id",
            unique=True,
            postgresql_where=sql_text("is_current"),
        ),
        Index(
            "ix_material_analyses_facets",
            "account_id",
            "format",
            "hook_type",
            "structure",
            postgresql_where=sql_text("is_current"),
        ),
        Index(
            "ix_material_analyses_devices",
            "devices",
            postgresql_using="gin",
            postgresql_where=sql_text("is_current"),
        ),
        Index(
            "ix_material_analyses_features",
            "features",
            postgresql_using="gin",
            postgresql_ops={"features": "jsonb_path_ops"},
            postgresql_where=sql_text("is_current"),
        ),
        _fk(["source_id"], "sources", "fk_material_analyses_source"),
        one_of("format", ANALYSIS_FORMATS),
        {
            "comment": "Разбор материала по полочкам. Производное: при новой версии промпта или"
            " таксономии создаётся новая строка, старая получает is_current = false"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    source_id: Mapped[uuid.UUID] = mapped_column(comment="Материал")
    analyzer_version: Mapped[str] = mapped_column(comment="Ключ и версия промпта разбора")
    taxonomy_version: Mapped[int] = mapped_column(
        comment="Версия таксономии, в которой сделан разбор"
    )
    model: Mapped[str] = mapped_column(comment="Модель, сделавшая разбор")
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(comment="Вызов в журнале llm_calls")
    is_current: Mapped[bool] = mapped_column(
        server_default=sql_text("true"), comment="Действующий разбор материала"
    )
    format: Mapped[str] = mapped_column(comment="Формат: post, thread, reply, quote, article")
    archetype: Mapped[str | None] = mapped_column(comment="Жанр (таксономия archetype)")
    lang: Mapped[str | None] = mapped_column(comment="Язык текста")
    hook_type: Mapped[str | None] = mapped_column(comment="Тип хука (таксономия hook_type)")
    hook_text: Mapped[str | None] = mapped_column(
        comment="Первая строка или фраза-хук — дословная цитата из материала, проверяется кодом"
    )
    structure: Mapped[str | None] = mapped_column(comment="Структура (таксономия structure)")
    structure_outline: Mapped[list[Any]] = jsonb("Шаги структуры: [{step, role, quote}]", "[]")
    tone: Mapped[list[str]] = _text_array("Тон (таксономия tone), до трёх значений")
    devices: Mapped[list[str]] = _text_array("Приёмы удержания (таксономия device)")
    closer: Mapped[str | None] = mapped_column(comment="Тип концовки (таксономия closer)")
    human_markers: Mapped[list[str]] = _text_array("Что делает текст живым: дословные фрагменты")
    ai_markers: Mapped[list[str]] = _text_array("Что звучит как AI: дословные фрагменты")
    features: Mapped[dict[str, Any]] = jsonb(
        "Признаки, посчитанные кодом: длина, число строк, цифра в первой строке, вопрос, эмодзи,"
        " ссылка, медиа, первое лицо и т. д."
    )
    why_hypotheses: Mapped[list[Any]] = jsonb(
        "Гипотезы «почему зашло или не зашло»: [{claim, quote}]. Не доказательство", "[]"
    )
    declared_claims: Mapped[list[Any]] = jsonb(
        "Утверждения о ремесле, которые материал заявляет сам (для статей и разборов)", "[]"
    )
    proposed_terms: Mapped[list[Any]] = jsonb("Предложенные новые значения таксономии", "[]")
    quality_flags: Mapped[list[str]] = _text_array(
        "Флаги качества: truncated, low_content, non_text, parent_missing"
    )
    created_at: Mapped[datetime] = created_at("Создано")


# --- знания -------------------------------------------------------------------------------


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        UniqueConstraint("account_id", "id"),
        _fk(["merged_into"], "knowledge_items", "fk_knowledge_items_merged_into"),
        Index(
            "ix_knowledge_items_pack",
            "account_id",
            "layer",
            "element",
            sql_text("quality DESC"),
            postgresql_where=sql_text(
                "status IN ('active', 'probation') AND owner_state <> 'disabled'"
            ),
        ),
        Index("ix_knowledge_items_status", "account_id", "status", sql_text("updated_at DESC")),
        Index(
            "ix_knowledge_items_parent",
            "parent_item_id",
            postgresql_where=sql_text("parent_item_id IS NOT NULL"),
        ),
        Index(
            "ix_knowledge_items_trgm",
            "statement",
            postgresql_using="gin",
            postgresql_ops={"statement": "gin_trgm_ops"},
        ),
        one_of("kind", ITEM_KINDS),
        one_of("layer", ITEM_LAYERS),
        one_of("element", ITEM_ELEMENTS),
        CheckConstraint("char_length(statement) BETWEEN 15 AND 240", name="statement_len"),
        CheckConstraint("char_length(guidance) BETWEEN 10 AND 200", name="guidance_len"),
        CheckConstraint("char_length(skeleton) <= 600", name="skeleton_len"),
        one_of("target_metric", TARGET_METRICS),
        one_of("risk_class", RISK_CLASSES),
        one_of("status", ITEM_STATUSES),
        one_of("owner_state", OWNER_STATES),
        one_of("origin", ITEM_ORIGINS),
        CheckConstraint("(status = 'merged') = (merged_into IS NOT NULL)", name="merged_into"),
        CheckConstraint(
            "detector ->> 'type' IN ('feature', 'rubric', 'none')", name="detector_type"
        ),
        CheckConstraint("layer <> 'voice' OR risk_class = 'A'", name="voice_is_class_a"),
        CheckConstraint("kind <> 'lexicon' OR layer = 'voice'", name="lexicon_is_voice"),
        {
            "comment": "Единица знания: тезис, паттерн хука, структура, анти-паттерн, словарь,"
            " пример-эталон. Текущее состояние; история текста — в knowledge_item_versions,"
            " история весов — в knowledge_snapshots"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант (global для общих шаблонов)")
    kind: Mapped[str] = mapped_column(
        comment="Тип знания: thesis, hook_pattern, structure, anti_pattern, lexicon, exemplar"
    )
    layer: Mapped[str] = mapped_column(
        comment="Слой: voice — только мой голос; humanness — живой текст против AI; craft —"
        " переносимое ремесло; niche — конвенции ниши; platform — механика X"
    )
    element: Mapped[str] = mapped_column(
        comment="К какой части текста относится: hook, structure, device, closer, tone,"
        " lexicon, packaging, timing, general"
    )
    statement: Mapped[str] = mapped_column(
        comment="Проверяемое утверждение для владельца, 15–240 знаков"
    )
    guidance: Mapped[str] = mapped_column(
        comment="Как применять — короткое указание для модели-автора, 10–200 знаков"
    )
    skeleton: Mapped[str | None] = mapped_column(
        comment="Шаблон со слотами для structure и exemplar. Чужой дословный текст сюда не попадает"
    )
    target_metric: Mapped[str] = mapped_column(
        server_default=sql_text("'engagement'"),
        comment="На какую цель работает знание: reach, engagement, replies, saves; quality —"
        " про качество текста, без исхода",
    )
    detector: Mapped[dict[str, Any]] = mapped_column(
        server_default=sql_text("""'{"type": "none"}'::jsonb"""),
        comment="Как распознать знание в тексте: {type: feature, all: [{path, op, value}]} —"
        " по полям разбора; {type: rubric, question} — вопрос судье; {type: none}",
    )
    risk_class: Mapped[str] = mapped_column(
        CHAR(1),
        comment="A — меняет голос, только с подтверждением; B — ремесло, испытание в одном"
        " варианте; C — мягкий запрет, предупреждение линтера",
    )
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'candidate'"),
        comment="Жизненный цикл: candidate, probation, active, weakened, conflicted, archived,"
        " rejected, merged",
    )
    owner_state: Mapped[str] = mapped_column(
        server_default=sql_text("'none'"),
        comment="Решение владельца: none, confirmed, pinned (всегда в пакете), disabled (не"
        " применять)",
    )
    origin: Mapped[str] = mapped_column(
        comment="Откуда появилось: materials, edits (правки черновиков), own_metrics, owner"
        " (ввёл сам), seed"
    )
    parent_item_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Общий шаблон из аккаунта global, от которого создана копия"
    )
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        comment="В какое знание влито (для status = merged)"
    )
    version: Mapped[int] = mapped_column(
        server_default=sql_text("1"), comment="Номер действующей версии текста"
    )
    eff_alpha: Mapped[Decimal] = _num(
        "Результативность: априор 1 + сумма весов подтверждений", server_default=sql_text("1")
    )
    eff_beta: Mapped[Decimal] = _num(
        "Результативность: априор 1 + сумма весов опровержений", server_default=sql_text("1")
    )
    fit_alpha: Mapped[Decimal] = _num(
        "Совместимость с владельцем: априор 2 + сумма весов принятий",
        server_default=sql_text("2"),
    )
    fit_beta: Mapped[Decimal] = _num(
        "Совместимость: априор 1 + сумма весов отказов", server_default=sql_text("1")
    )
    eff_conf: Mapped[Decimal] = _num(
        "Уверенность в результативности: среднее минус одно стандартное отклонение, 0..1",
        server_default=sql_text("0.2113"),
    )
    fit_conf: Mapped[Decimal] = _num(
        "Уверенность в совместимости, 0..1", server_default=sql_text("0.4310")
    )
    quality: Mapped[Decimal] = _num(
        "Итоговое качество для ранжирования: 0.6·eff_conf + 0.4·fit_conf (для target_metric ="
        " quality — только fit_conf)",
        server_default=sql_text("0.2992"),
    )
    evidence_n: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Число доказательств результативности с исходом"
    )
    independent_authors: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Число разных авторов среди подтверждений"
    )
    own_support: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Подтверждений на своих постах"
    )
    own_contra: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Опровержений на своих постах"
    )
    occurrences: Mapped[int] = mapped_column(
        server_default=sql_text("0"), comment="Сколько раз знание встретилось в материалах вообще"
    )
    last_evidence_at: Mapped[datetime | None] = mapped_column(
        comment="Дата самого свежего доказательства"
    )
    scores_updated_at: Mapped[datetime | None] = mapped_column(comment="Когда пересчитаны веса")
    probation_started_at: Mapped[datetime | None] = mapped_column(
        comment="Когда знание взято на испытание"
    )
    created_at: Mapped[datetime] = created_at("Создано")
    updated_at: Mapped[datetime] = updated_at("Изменено")


class KnowledgeItemVersion(Base):
    __tablename__ = "knowledge_item_versions"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "version"),
        _fk(["item_id"], "knowledge_items", "fk_knowledge_item_versions_item"),
        Index("ix_knowledge_item_versions_account", "account_id", "item_id"),
        one_of("changed_by", ACTORS),
        {
            "comment": "Все версии формулировки знания. Правка создаёт новую версию, старая не"
            " меняется"
        },
    )

    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    version: Mapped[int] = mapped_column(comment="Номер версии")
    statement: Mapped[str] = mapped_column(comment="Утверждение в этой версии")
    guidance: Mapped[str] = mapped_column(comment="Указание в этой версии")
    skeleton: Mapped[str | None] = mapped_column(comment="Шаблон в этой версии")
    detector: Mapped[dict[str, Any]] = mapped_column(comment="Детектор в этой версии")
    kind: Mapped[str] = mapped_column(comment="Тип знания в этой версии")
    layer: Mapped[str] = mapped_column(comment="Слой в этой версии")
    element: Mapped[str] = mapped_column(comment="Элемент в этой версии")
    target_metric: Mapped[str] = mapped_column(comment="Цель в этой версии")
    changed_by: Mapped[str] = mapped_column(comment="Кто изменил: system или owner")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), comment="Пользователь, если изменил владелец"
    )
    reason: Mapped[str] = mapped_column(comment="Причина: extracted, merged, owner_edit, rollback")
    provenance: Mapped[dict[str, Any]] = jsonb(
        "Происхождение: промпт, вызов модели, id материалов-входов"
    )
    created_at: Mapped[datetime] = created_at("Создано")


class KnowledgeItemScope(Base):
    __tablename__ = "knowledge_item_scopes"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "facet", "value"),
        _fk(["item_id"], "knowledge_items", "fk_knowledge_item_scopes_item", ondelete="CASCADE"),
        Index("ix_knowledge_scopes_lookup", "account_id", "facet", "value", "item_id"),
        one_of("facet", SCOPE_FACETS),
        {
            "comment": "Где знание применимо. Одно знание — много строк; нет строк по измерению —"
            " применимо ко всем его значениям"
        },
    )

    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    facet: Mapped[str] = mapped_column(comment="Измерение: format, goal, niche, lang, archetype")
    value: Mapped[str] = mapped_column(comment="Значение измерения (ключ таксономии)")


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"
    __table_args__ = (
        _fk(["item_id"], "knowledge_items", "fk_knowledge_evidence_item"),
        _fk(["source_id"], "sources", "fk_knowledge_evidence_source"),
        _fk(["author_id"], "authors", "fk_knowledge_evidence_author"),
        Index(
            "uq_evidence_source",
            "item_id",
            "source_id",
            "basis",
            "dimension",
            unique=True,
            postgresql_where=sql_text("revoked_at IS NULL AND source_id IS NOT NULL"),
        ),
        Index(
            "uq_evidence_draft",
            "item_id",
            "draft_id",
            "basis",
            "dimension",
            unique=True,
            postgresql_where=sql_text(
                "revoked_at IS NULL AND draft_id IS NOT NULL AND source_id IS NULL"
            ),
        ),
        Index(
            "ix_evidence_item",
            "account_id",
            "item_id",
            "dimension",
            postgresql_where=sql_text("revoked_at IS NULL"),
        ),
        Index(
            "ix_evidence_source",
            "account_id",
            "source_id",
            postgresql_where=sql_text("source_id IS NOT NULL"),
        ),
        Index("ix_evidence_time", "account_id", "created_at"),
        one_of("dimension", EVIDENCE_DIMENSIONS),
        one_of("basis", EVIDENCE_BASES),
        CheckConstraint("polarity IN (-1, 0, 1)", name="polarity_allowed"),
        _between("detector_score", 0, 1),
        _between("w_source", 0, 3),
        _between("w_scope", 0, 2),
        _between("w_magnitude", 0, 1),
        _between("w_author", 0, 1),
        CheckConstraint(
            "source_id IS NOT NULL OR draft_id IS NOT NULL OR basis = 'owner_decision'",
            name="has_subject",
        ),
        CheckConstraint("(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revoked_reason"),
        CheckConstraint(
            "basis NOT IN ('review', 'owner_decision') OR dimension = 'fit'",
            name="decisions_are_fit",
        ),
        {
            "comment": "Доказательства за и против знания. Строки не меняются и не удаляются:"
            " ошибочное доказательство отзывается (revoked_at). Из этой таблицы веса"
            " пересчитываются на любую дату"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Материал-доказательство (свой или чужой пост)"
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("drafts.id"),
        comment="Черновик, если доказательство — решение владельца по черновику",
    )
    dimension: Mapped[str] = mapped_column(
        comment="eff — результативность (исходы постов); fit — совместимость с владельцем (его"
        " решения)"
    )
    basis: Mapped[str] = mapped_column(
        comment="Основание: observed_outcome — исход чужого поста; own_outcome — исход своего;"
        " declared — заявлено в статье; owner_pick — владелец отобрал; review — решение по"
        " черновику; owner_decision — подтверждение знания"
    )
    polarity: Mapped[int] = mapped_column(
        SmallInteger,
        comment="+1 подтверждает, −1 опровергает, 0 — знание встретилось, исход нейтрален",
    )
    detector_score: Mapped[Decimal | None] = _num(
        "Насколько уверенно знание распознано в тексте, 0..1"
    )
    outcome_tier: Mapped[str | None] = mapped_column(
        comment="Исход материала по цели знания на момент записи"
    )
    target_metric: Mapped[str | None] = mapped_column(comment="Цель, по которой брался исход")
    format: Mapped[str | None] = mapped_column(
        comment="Формат материала — для разреза весов по форматам"
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Автор материала — для счёта независимых авторов"
    )
    w_source: Mapped[Decimal] = _num("Надёжность источника исхода")
    w_scope: Mapped[Decimal] = _num(
        "Близость: свой пост 2.0, своя ниша 1.0, ниша неизвестна 0.8, чужая ниша 0.5"
    )
    w_magnitude: Mapped[Decimal] = _num("Сила исхода: hit и flop 1.0, above и below 0.5, normal 0")
    w_author: Mapped[Decimal] = _num(
        "Скидка за повтор автора: 1/k для k-го доказательства от того же автора",
        server_default=sql_text("1"),
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric,
        Computed("w_source * w_scope * w_magnitude * w_author", persisted=True),
        comment="Итоговый вес без учёта свежести (свежесть применяется при пересчёте)",
    )
    observed_at: Mapped[datetime] = mapped_column(
        comment="Дата события, от которой считается свежесть: выход поста или решение владельца"
    )
    created_at: Mapped[datetime] = created_at("Когда доказательство записано")
    revoked_at: Mapped[datetime | None] = mapped_column(comment="Когда отозвано")
    revoked_reason: Mapped[str | None] = mapped_column(
        comment="Почему отозвано: source_removed, author_blocked, rollback, reanalysis, owner"
    )


class KnowledgeLink(Base):
    __tablename__ = "knowledge_links"
    __table_args__ = (
        _fk(["from_item_id"], "knowledge_items", "fk_knowledge_links_from_item"),
        _fk(["to_item_id"], "knowledge_items", "fk_knowledge_links_to_item"),
        UniqueConstraint("from_item_id", "to_item_id", "relation"),
        Index(
            "ix_knowledge_links_open",
            "account_id",
            "relation",
            postgresql_where=sql_text("status = 'open'"),
        ),
        Index("ix_knowledge_links_to", "account_id", "to_item_id"),
        one_of("relation", LINK_RELATIONS),
        _between("confidence", 0, 1),
        one_of("decided_by", LINK_DECIDERS),
        one_of("status", LINK_STATUSES),
        CheckConstraint("from_item_id <> to_item_id", name="not_self"),
        {
            "comment": "Связи знаний: дубль, уточнение, обобщение, противоречие. Открытое"
            " противоречие убирает обе стороны из пакета до решения"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    from_item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание-источник связи")
    to_item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание-цель связи")
    relation: Mapped[str] = mapped_column(
        comment="Тип связи: duplicates, refines, generalizes, contradicts, related"
    )
    confidence: Mapped[Decimal] = _num("Уверенность судьи в связи")
    decided_by: Mapped[str] = mapped_column(comment="Кто установил связь: judge, owner, system")
    status: Mapped[str] = mapped_column(
        server_default=sql_text("'open'"), comment="open — требует решения; resolved — решено"
    )
    resolution: Mapped[str | None] = mapped_column(
        comment="Как решено: kept_from, kept_to, split_scope, both"
    )
    created_at: Mapped[datetime] = created_at("Создано")
    resolved_at: Mapped[datetime | None] = mapped_column(comment="Когда решено")


class KnowledgeTransition(Base):
    __tablename__ = "knowledge_transitions"
    __table_args__ = (
        _fk(["item_id"], "knowledge_items", "fk_knowledge_transitions_item"),
        Index("ix_knowledge_transitions_item", "account_id", "item_id", sql_text("ts DESC")),
        Index(
            "ix_knowledge_transitions_auto",
            "account_id",
            sql_text("ts DESC"),
            postgresql_where=sql_text("actor = 'system'"),
        ),
        one_of("actor", ACTORS),
        {
            "comment": "Каждый переход статуса знания: кто, по какому правилу, с какими числами."
            " Основа отката и недельной сводки"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    ts: Mapped[datetime] = created_at("Когда")
    from_status: Mapped[str | None] = mapped_column(comment="Прежний статус (NULL при создании)")
    to_status: Mapped[str] = mapped_column(comment="Новый статус")
    actor: Mapped[str] = mapped_column(comment="system — автоматически; owner — решение владельца")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), comment="Пользователь, если решил владелец"
    )
    rule: Mapped[str] = mapped_column(
        comment="Правило перехода: auto_probation, promote_by_reviews, promote_by_own_evidence,"
        " weaken_low_quality, archive_stale, conflict_opened, owner_confirm, owner_reject,"
        " rollback"
    )
    reason: Mapped[str | None] = mapped_column(comment="Пояснение для владельца")
    payload: Mapped[dict[str, Any]] = jsonb("Числа на момент перехода: веса, счётчики, пороги")
    reverted_at: Mapped[datetime | None] = mapped_column(comment="Когда переход откатили")
    reverted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), comment="Кто откатил"
    )


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "model_id"),
        _fk(["item_id"], "knowledge_items", "fk_knowledge_embeddings_item", ondelete="CASCADE"),
        Index("ix_knowledge_embeddings_account", "account_id", "item_id"),
        Index(
            "ix_knowledge_embeddings_hnsw",
            "vec",
            postgresql_using="hnsw",
            postgresql_ops={"vec": "vector_cosine_ops"},
        ),
        {
            "comment": "Вектор текста знания (statement + guidance) для поиска похожих и подбора"
            " под задачу"
        },
    )

    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("embedding_models.id"), comment="Модель эмбеддингов"
    )
    version: Mapped[int] = mapped_column(comment="Версия текста знания, по которой посчитан вектор")
    vec: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), comment="Вектор, 384 измерения")


class KnowledgeSnapshot(Base):
    __tablename__ = "knowledge_snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("snapshot_date", "account_id", "item_id"),
        Index(
            "ix_knowledge_snapshots_item", "account_id", "item_id", sql_text("snapshot_date DESC")
        ),
        {
            "comment": "Состояние каждого знания на конец дня. Партиции по месяцам. Источник"
            " метрик «Мозга», сравнения «неделя к неделе» и отката на дату",
            "postgresql_partition_by": "RANGE (snapshot_date)",
        },
    )

    snapshot_date: Mapped[date] = mapped_column(Date, comment="Дата снимка (по поясу аккаунта)")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    status: Mapped[str] = mapped_column(comment="Статус на дату")
    owner_state: Mapped[str] = mapped_column(comment="Решение владельца на дату")
    version: Mapped[int] = mapped_column(comment="Версия текста на дату")
    layer: Mapped[str] = mapped_column(comment="Слой знания")
    eff_alpha: Mapped[Decimal] = _num("eff_alpha на дату")
    eff_beta: Mapped[Decimal] = _num("eff_beta на дату")
    fit_alpha: Mapped[Decimal] = _num("fit_alpha на дату")
    fit_beta: Mapped[Decimal] = _num("fit_beta на дату")
    eff_conf: Mapped[Decimal] = _num("eff_conf на дату")
    fit_conf: Mapped[Decimal] = _num("fit_conf на дату")
    quality: Mapped[Decimal] = _num("quality на дату")
    evidence_n: Mapped[int] = mapped_column(comment="Число доказательств на дату")
    own_support: Mapped[int] = mapped_column(comment="Подтверждений на своих постах на дату")
    own_contra: Mapped[int] = mapped_column(comment="Опровержений на своих постах на дату")


# --- выдача -------------------------------------------------------------------------------


class KnowledgePack(Base):
    __tablename__ = "knowledge_packs"
    __table_args__ = (
        UniqueConstraint("account_id", "id"),
        Index("ix_knowledge_packs_time", "account_id", sql_text("created_at DESC")),
        one_of("consumer", PACK_CONSUMERS),
        _nonneg("token_count"),
        {
            "comment": "Пакет знаний, выданный под задачу. Хранит id и порядок знаний, чтобы"
            " любой черновик можно было объяснить и воспроизвести"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    consumer: Mapped[str] = mapped_column(comment="Кому выдан: writer, export, mcp, eval")
    task: Mapped[dict[str, Any]] = mapped_column(
        comment="Задача (TaskSpec): формат, цель, ниша, язык, жанр, тема"
    )
    schema_version: Mapped[int] = mapped_column(comment="Версия схемы KnowledgePack")
    taxonomy_version: Mapped[int] = mapped_column(comment="Версия таксономии на момент сборки")
    items: Mapped[list[Any]] = mapped_column(
        comment="Состав: [{item_id, version, slot, rank, score}]"
    )
    token_count: Mapped[int] = mapped_column(comment="Оценка размера пакета в токенах")
    created_at: Mapped[datetime] = created_at("Создано")


class KnowledgeApplication(Base):
    __tablename__ = "knowledge_applications"
    __table_args__ = (
        _fk(["pack_id"], "knowledge_packs", "fk_knowledge_applications_pack"),
        _fk(["item_id"], "knowledge_items", "fk_knowledge_applications_item"),
        UniqueConstraint("draft_id", "item_id"),
        Index(
            "ix_knowledge_applications_item",
            "account_id",
            "item_id",
            sql_text("created_at DESC"),
        ),
        one_of("slot", APPLICATION_SLOTS),
        _between("applied_score", 0, 1),
        one_of("checked_by", CHECKERS),
        {
            "comment": "Какие знания были даны автору для черновика и какие реально применены в"
            " тексте. Связывает знание с решением владельца и с исходом поста"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    pack_id: Mapped[uuid.UUID] = mapped_column(comment="Пакет, из которого пришло знание")
    item_id: Mapped[uuid.UUID] = mapped_column(comment="Знание")
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drafts.id"), comment="Черновик")
    slot: Mapped[str] = mapped_column(
        comment="Роль в пакете: core, probation (испытание), anti (чего избегать), pinned"
    )
    applied: Mapped[bool | None] = mapped_column(
        comment="Применено ли знание в тексте: true, false, NULL — не проверялось"
    )
    applied_score: Mapped[Decimal | None] = _num("Уверенность детектора, 0..1")
    checked_by: Mapped[str | None] = mapped_column(
        comment="Чем проверено: feature (код) или rubric (судья)"
    )
    checked_at: Mapped[datetime | None] = mapped_column(comment="Когда проверено")
    created_at: Mapped[datetime] = created_at("Создано")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("account_id", "key"),
        CheckConstraint(KEY_PATTERN, name="key_format"),
        {
            "comment": "Сегмент — это фильтр по слою, элементу и области действия знаний."
            " Знание попадает в несколько сегментов без дублирования"
        },
    )

    id: Mapped[uuid.UUID] = uuid_pk("UUIDv7")
    account_id: Mapped[uuid.UUID] = account_fk("Тенант")
    key: Mapped[str] = mapped_column(comment="Ключ сегмента")
    title: Mapped[str] = mapped_column(comment="Название в боте")
    filter: Mapped[dict[str, Any]] = mapped_column(
        comment="Фильтр: {layer: [...], element: [...], kind: [...], format: [...], goal:"
        " [...], niche: [...], lang: [...]}. Пустой ключ — без ограничения"
    )
    position: Mapped[int] = mapped_column(server_default=sql_text("0"), comment="Порядок в меню")
    is_default: Mapped[bool] = mapped_column(
        server_default=sql_text("false"), comment="Создан системой при онбординге"
    )
    created_at: Mapped[datetime] = created_at("Создано")
