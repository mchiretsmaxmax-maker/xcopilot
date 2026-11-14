"""Признаки материала, посчитанные кодом (ТЗ v3.1, шаг 3 конвейера; этап 7A).

Ключи совпадают с `features.*` в ALLOWED_PATHS (приложение D): по ним строятся детекторы.
"""

from __future__ import annotations

from collections.abc import Sequence

FeatureValue = int | float | bool


def compute_features(
    text: str,
    kind: str,
    *,
    parts: Sequence[str] | None = None,
    has_media: bool = False,
) -> dict[str, FeatureValue]:
    raise NotImplementedError
