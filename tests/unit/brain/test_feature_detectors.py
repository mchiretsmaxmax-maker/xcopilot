"""Детекторы feature (этап 9A): условия по разрешённым полям разбора, кодом."""

from typing import Any

import pytest
from pydantic import ValidationError

from xcopilot.brain.detectors import eval_feature
from xcopilot.brain.schemas import FeatureDetector

ANALYSIS: dict[str, Any] = {
    "format": "post",
    "hook_type": "number",
    "structure": "list",
    "devices": ["contrast", "callback"],
    "tone": ["calm"],
    "features": {"char_len": 240, "has_question": True, "emoji_count": 0},
}


def det(*conds: dict[str, Any]) -> FeatureDetector:
    return FeatureDetector.model_validate({"type": "feature", "all": list(conds)})


def test_feature_detector_ops_eq_in_contains_gte() -> None:
    assert eval_feature(det({"path": "hook_type", "op": "eq", "value": "number"}), ANALYSIS) == 1.0
    assert (
        eval_feature(det({"path": "hook_type", "op": "eq", "value": "question"}), ANALYSIS) == 0.0
    )
    assert (
        eval_feature(det({"path": "structure", "op": "in", "value": ["list", "story"]}), ANALYSIS)
        == 1.0
    )
    assert eval_feature(det({"path": "structure", "op": "in", "value": ["story"]}), ANALYSIS) == 0.0
    assert (
        eval_feature(det({"path": "devices", "op": "contains", "value": "contrast"}), ANALYSIS)
        == 1.0
    )
    assert eval_feature(det({"path": "devices", "op": "contains", "value": "pun"}), ANALYSIS) == 0.0
    assert (
        eval_feature(det({"path": "features.char_len", "op": "gte", "value": 200}), ANALYSIS) == 1.0
    )
    assert (
        eval_feature(det({"path": "features.char_len", "op": "gte", "value": 300}), ANALYSIS) == 0.0
    )
    assert (
        eval_feature(det({"path": "features.char_len", "op": "lte", "value": 300}), ANALYSIS) == 1.0
    )
    assert eval_feature(det({"path": "features.has_question", "op": "is_true"}), ANALYSIS) == 1.0
    assert eval_feature(det({"path": "features.has_question", "op": "is_false"}), ANALYSIS) == 0.0
    # все условия сразу: одно ложное — детектор не срабатывает
    both = det(
        {"path": "hook_type", "op": "eq", "value": "number"},
        {"path": "features.emoji_count", "op": "gte", "value": 1},
    )
    assert eval_feature(both, ANALYSIS) == 0.0
    # поле отсутствует в разборе — условие не выполнено, а не ошибка
    assert eval_feature(det({"path": "features.has_media", "op": "is_true"}), ANALYSIS) == 0.0
    # детектор из JSONB (dict) понимается так же
    raw = {"type": "feature", "all": [{"path": "format", "op": "eq", "value": "post"}]}
    assert eval_feature(raw, ANALYSIS) == 1.0


def test_feature_detector_unknown_path_rejected() -> None:
    with pytest.raises(ValidationError):
        det({"path": "text", "op": "contains", "value": "airdrop"})
    with pytest.raises(ValueError, match="unknown detector path"):
        eval_feature(
            {"type": "feature", "all": [{"path": "author_handle", "op": "eq", "value": "x"}]},
            ANALYSIS,
        )
