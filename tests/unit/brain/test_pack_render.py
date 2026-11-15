"""Рендер пакета знаний (этап 9B, ТЗ 4.3): Markdown и JSON, без цифр уверенности."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from xcopilot.brain.render import to_json, to_markdown
from xcopilot.brain.schemas import KnowledgePack, PackItem, TaskSpec

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def pi(rank: int, slot: str, text: str, **kw: object) -> PackItem:
    base: dict[str, object] = {
        "item_id": uuid4(),
        "version": 7,
        "slot": slot,
        "rank": rank,
        "kind": "hook_pattern",
        "layer": "craft",
        "element": "hook",
        "guidance": text,
        "statement": f"Наблюдение: {text}",
        "skeleton": None,
        "confidence": "high",
        "proven_on_own_posts": True,
        "score": 0.8734,
    }
    base.update(kw)
    return PackItem.model_validate(base)


def pack(items: list[PackItem], fmt: str = "reply") -> KnowledgePack:
    return KnowledgePack(
        pack_id=uuid4(),
        account_id=uuid4(),
        task=TaskSpec(format=fmt, goal="replies", lang="en", niche="crypto", topic="про провалы"),
        taxonomy_version=3,
        items=items,
        token_count=120,
        built_at=NOW,
    )


def sample() -> KnowledgePack:
    return pack(
        [
            pi(1, "pinned", "Начинай с вывода, детали потом."),
            pi(2, "core", "Начни с конкретной цифры из своего опыта.", confidence="high"),
            pi(
                3,
                "core",
                "Возрази одному утверждению из поста.",
                confidence="medium",
                proven_on_own_posts=False,
            ),
            pi(
                4,
                "core",
                "Каркас без цифр",
                kind="structure",
                element="structure",
                skeleton="[согласие] → [чего не хватает] → [свой пример]",
                confidence="medium",
                proven_on_own_posts=False,
            ),
            pi(5, "core", "Одна мысль на строку.", kind="thesis", element="device"),
            pi(
                6,
                "core",
                "Заверши наблюдением.",
                kind="thesis",
                element="closer",
                confidence="low",
                proven_on_own_posts=False,
            ),
            pi(
                7, "core", "Пиши в рабочие часы.", kind="thesis", element="timing", layer="platform"
            ),
            pi(
                8,
                "anti",
                "Не заканчивай вопросом ради вопроса.",
                kind="anti_pattern",
                element="closer",
            ),
            pi(
                9,
                "probation",
                "Продолжи мысль автора.",
                confidence="low",
                proven_on_own_posts=False,
            ),
        ]
    )


def test_markdown_has_no_digits_in_confidence() -> None:
    md = to_markdown(sample())
    assert not re.search(r"\d", md.replace("варианта 3", "")), md
    assert "уверенность высокая; проверено на постах владельца" in md
    assert "уверенность средняя" in md
    assert "уверенность низкая" in md


def test_markdown_sections_and_order() -> None:
    md = to_markdown(sample())
    heads = re.findall(r"^### (.+)$", md, re.M)
    assert heads == [
        "Закреплено владельцем",
        "Первая фраза",
        "Структура",
        "Приёмы",
        "Концовка",
        "Площадка и упаковка",
        "Чего избегать",
        "Испытание — только для варианта 3",
    ]
    assert md.startswith("## Знания под задачу:")
    assert "Жёсткие правила паспорта важнее любого пункта ниже." in md
    assert "Каркас: [согласие] → [чего не хватает] → [свой пример]" in md


def test_markdown_empty_pack_has_header_only() -> None:
    md = to_markdown(pack([]))
    assert md.startswith("## Знания под задачу:")
    assert "###" not in md


def test_render_is_deterministic_and_ignores_ids() -> None:
    p = sample()
    other = p.model_copy(update={"pack_id": uuid4(), "built_at": NOW.replace(year=2027)})
    assert to_markdown(p) == to_markdown(other)


def test_json_roundtrips_to_pack() -> None:
    p = sample()
    raw = to_json(p)
    assert KnowledgePack.model_validate_json(raw) == p
    assert json.loads(raw)["schema_version"] == 1
