"""Файл анти-AI правил v1: разбирается и содержит RU и EN (ТЗ v3.1; в БД грузит этап 9A)."""

from __future__ import annotations

from xcopilot.style.global_rules import read_seed


def test_seed_file_parses_and_has_ru_and_en() -> None:
    rules = read_seed()
    assert len(rules) >= 20
    assert {r.lang for r in rules} == {"ru", "en"}
    assert {r.kind for r in rules} <= {"do", "dont", "pattern"}
    assert all(r.text and r.version == 1 for r in rules)
    texts = " ".join(r.text for r in rules).lower()
    assert "delve" in texts
    assert "в современном мире" in texts
    assert len({r.text for r in rules}) == len(rules)
