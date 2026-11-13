"""Резервный судья: те же вопросы текстовой модели (cheap) и разбор её JSON в ответы Verdict.

Модель отвечает точечно (вероятность «да», один вариант, один уровень шкалы) и уверенностью.
Вероятности по вариантам и уровням восстанавливаются так: выбранному — уверенность, остальным
поровну остаток. Так ответ резерва имеет ту же форму, что и ответ Jev.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from xcopilot.llm.schemas import Answer, ChoiceA, ChoiceQ, NoulA, NoulQ, Question, ScoreA
from xcopilot.llm.untrusted import wrap_untrusted

PROMPT_KEY = "judge.fallback"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> str:
    """JSON из ответа модели: содержимое ```-блока или от первой { до последней }."""
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text.strip()


def prompt_variables(state: str, questions: Mapping[str, Question]) -> dict[str, str]:
    """Переменные промпта judge.fallback: текст как недоверенные данные и вопросы в JSON."""
    described: dict[str, dict[str, Any]] = {}
    for name, q in questions.items():
        item: dict[str, Any] = {"type": q.kind, "instructions": q.instructions}
        if isinstance(q, NoulQ):
            item.update({k: v for k, v in (("yes", q.yes), ("no", q.no)) if v is not None})
        elif isinstance(q, ChoiceQ):
            item["options"] = q.options
        else:
            item["levels"] = {str(i): level for i, level in enumerate(q.levels)}
        described[name] = item
    return {
        "state": wrap_untrusted(state, "state"),
        "questions": json.dumps(described, ensure_ascii=False, indent=1),
    }


def _spread(chosen: Any, keys: list[Any], confidence: float) -> dict[Any, float]:
    rest = (1 - confidence) / (len(keys) - 1)
    return {k: (confidence if k == chosen else rest) for k in keys}


def _confidence(raw: Mapping[str, Any], name: str) -> float:
    value = float(raw.get("confidence", 0.5))
    if not 0 <= value <= 1:
        raise ValueError(f"{name}: confidence должна быть от 0 до 1")
    return value


def parse_answers(text: str, questions: Mapping[str, Question]) -> dict[str, Answer]:
    """Разобрать ответ резервного судьи; любое несоответствие вопросам — ValueError."""
    data = json.loads(extract_json(text))
    raw_answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(raw_answers, dict):
        raise ValueError('нужен объект {"answers": {...}}')
    if missing := sorted(set(questions) - raw_answers.keys()):
        raise ValueError(f"нет ответов на вопросы: {', '.join(missing)}")
    answers: dict[str, Answer] = {}
    for name, q in questions.items():
        raw = raw_answers[name]
        if not isinstance(raw, dict):
            raise ValueError(f"{name}: ответ должен быть объектом")
        if isinstance(q, NoulQ):
            answers[name] = NoulA(p=float(raw["p"]))
        elif isinstance(q, ChoiceQ):
            choice = raw.get("choice")
            if choice not in q.options:
                raise ValueError(f"{name}: choice должен быть одним из {sorted(q.options)}")
            conf = _confidence(raw, name)
            answers[name] = ChoiceA(
                choice=choice, confidence=conf, probabilities=_spread(choice, list(q.options), conf)
            )
        else:
            score = raw.get("score")
            if not isinstance(score, int) or not 0 <= score < len(q.levels):
                raise ValueError(f"{name}: score должен быть целым от 0 до {len(q.levels) - 1}")
            conf = _confidence(raw, name)
            answers[name] = ScoreA(
                score=score,
                confidence=conf,
                probabilities=_spread(score, list(range(len(q.levels))), conf),
            )
    return answers
