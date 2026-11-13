"""Адаптер Jev (TypeSafe System One): перевод вопросов и разбор ответов."""

import json
from pathlib import Path

from xcopilot.llm.pricing import Usage
from xcopilot.llm.providers.jev import parse_system_one, to_jev_question
from xcopilot.llm.schemas import ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreA, ScoreQ

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "jev_systemone_response.json"


def test_jev_adapter_maps_question_types() -> None:
    levels = ["Нет крючка", "Слабый", "Хороший", "Сильный"]
    assert to_jev_question(ScoreQ(instructions="Насколько силён крючок?", levels=levels)) == {
        "type": "score",
        "instructions": "Насколько силён крючок?",
        "criteria": levels,  # порядок = уровни 0..3
    }
    assert to_jev_question(
        ChoiceQ(instructions="Тон?", options={"calm": "Спокойный", "angry": None})
    ) == {
        "type": "choice",
        "instructions": "Тон?",
        "criteria": {"calm": "Спокойный", "angry": None},
    }
    assert to_jev_question(NoulQ(instructions="По теме?")) == {
        "type": "noul",
        "instructions": "По теме?",
    }
    assert to_jev_question(NoulQ(instructions="Реклама?", yes="Есть призыв купить")) == {
        "type": "noul",
        "instructions": "Реклама?",
        "criteria": {"true": "Есть призыв купить"},
    }


def test_jev_adapter_parses_answers() -> None:
    result = parse_system_one(json.loads(FIXTURE.read_text()))
    assert result.model == "jev-2026-10-01"
    assert result.usage == Usage(input_tokens=120, output_tokens=12)
    assert result.answers["on_topic"] == NoulA(p=0.98)
    tone = result.answers["tone"]
    assert isinstance(tone, ChoiceA)
    assert tone.choice == "calm" and tone.probabilities["calm"] == 0.9
    hook = result.answers["hook"]
    assert isinstance(hook, ScoreA)
    assert hook.score == 1.7 and hook.confidence == 0.8
    assert hook.probabilities == {0: 0.05, 1: 0.25, 2: 0.65, 3: 0.05}
