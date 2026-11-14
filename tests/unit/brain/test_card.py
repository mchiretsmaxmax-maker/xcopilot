"""Карточка разбора (этап 7A, задача 8; ТЗ v3.1, раздел 5.2)."""

from __future__ import annotations

from uuid import uuid4

from tests.ingest_stand import buttons
from xcopilot.brain.card import CardView, render_card


def test_analysis_card_has_outcome_buttons() -> None:
    sid = uuid4()
    view = CardView(
        source_id=sid,
        format="post",
        author_handle="author",
        hook_title="личная цифра",
        hook_text="i lost $40k in 3 weeks",
        structure_title="история с поворотом",
        line_count=5,
        device_titles=["конкретная цифра", "короткие строки"],
        why="цифра и признание ошибки в первой строке",
        matched_knowledge=2,
        new_observations=1,
    )
    text, keyboard = render_card(view)
    assert text.startswith("🧩 Разбор · пост · @author")
    assert "Хук: личная цифра — «i lost $40k in 3 weeks»" in text
    assert "Структура: история с поворотом, 5 строк" in text
    assert "Приёмы: конкретная цифра, короткие строки" in text
    assert "Почему могло зайти (гипотеза): цифра и признание ошибки в первой строке" in text
    assert "Как он зашёл?" in text

    got = buttons(keyboard)
    assert ("🔥 Залетел", f"mo:{sid}:hit") in got
    assert ("👍 Норм", f"mo:{sid}:normal") in got
    assert ("👎 Не зашёл", f"mo:{sid}:flop") in got
    assert ("🔢 Цифры", f"mo:{sid}:nums") in got
    assert ("Это моё", f"ms:{sid}:own") in got
    assert ("Антипример", f"ms:{sid}:anti_example") in got
    assert ("🗑 Убрать", f"irm:{sid}") in got

    known_text, known_kb = render_card(
        CardView(
            source_id=sid,
            format="reply",
            author_handle=None,
            hook_title=None,
            hook_text="",
            structure_title=None,
            line_count=1,
            outcome_known=True,
        )
    )
    assert "Как он зашёл?" not in known_text
    assert not any(data.startswith("mo:") for _, data in buttons(known_kb))
    assert ("🗑 Убрать", f"irm:{sid}") in buttons(known_kb)
