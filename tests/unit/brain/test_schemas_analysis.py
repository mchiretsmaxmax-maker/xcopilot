"""Схема разбора и проверка цитат (этап 7A, задача 4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.brain_stand import analysis_json
from xcopilot.brain.schemas import MaterialAnalysis, verify_quotes

TEXT = "i lost $40k in 3 weeks\nthen i stopped trading memes\nnow i sleep fine"


def test_analysis_validates_schema() -> None:
    ok = MaterialAnalysis.model_validate_json(analysis_json(TEXT))
    assert ok.hook_type == "number_personal"

    with pytest.raises(ValidationError):  # лишнее поле
        MaterialAnalysis.model_validate_json(analysis_json(TEXT, verdict="great"))
    with pytest.raises(ValidationError):  # ключ словаря не по формату
        MaterialAnalysis.model_validate_json(analysis_json(TEXT, hook_type="Number Personal"))
    with pytest.raises(ValidationError):  # больше 2 предложений в словарь
        MaterialAnalysis.model_validate_json(
            analysis_json(
                TEXT,
                proposed_terms=[
                    {
                        "facet": "device",
                        "key": f"new_term_{i}",
                        "title": "новый приём",
                        "description": "описание нового приёма достаточной длины",
                        "quote": "i lost",
                    }
                    for i in range(3)
                ],
            )
        )
    with pytest.raises(ValidationError):  # не тот формат
        MaterialAnalysis.model_validate_json(analysis_json(TEXT, format="tweet"))


def test_fabricated_quotes_dropped() -> None:
    raw = MaterialAnalysis.model_validate_json(
        analysis_json(
            TEXT,
            hook_text="i made $1M in a day",
            outline=[
                {"role": "hook", "quote": "i lost $40k in 3 weeks"},
                {"role": "proof", "quote": "my portfolio doubled"},
            ],
            human_markers=["now i sleep fine", "i cried every night"],
            why=[
                {
                    "element": "hook",
                    "claim": "цифра в первой строке цепляет",
                    "quote": "I LOST $40K",
                },
                {"element": "closer", "claim": "концовка-вывод закрывает петлю", "quote": "fake"},
            ],
        )
    )
    checked = verify_quotes(raw, TEXT)
    assert checked.hook_text == ""
    assert [s.quote for s in checked.outline] == ["i lost $40k in 3 weeks"]
    assert checked.human_markers == ["now i sleep fine"]
    assert [w.quote for w in checked.why] == ["I LOST $40K"]  # регистр и пробелы не важны
