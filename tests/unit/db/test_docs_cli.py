"""CLI генератора документации схемы: запись и проверка актуальности."""

from pathlib import Path

import pytest

from xcopilot.db.docs import main

ROOT = Path(__file__).resolve().parents[3]


def test_check_passes_on_repo_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    assert main(["--check"]) == 0


def test_write_then_check_detects_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--check"]) == 1
    assert main([]) == 0
    assert "обновлено" in capsys.readouterr().out
    assert main(["--check"]) == 0
    schema = tmp_path / "docs/db/schema.md"
    schema.write_text(schema.read_text() + "правка руками\n")
    assert main(["--check"]) == 1
    assert "make db-docs" in capsys.readouterr().err
