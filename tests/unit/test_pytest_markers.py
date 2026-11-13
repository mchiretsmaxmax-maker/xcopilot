import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_live_and_eval_markers_excluded_by_default(tmp_path: Path) -> None:
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "def test_plain(): pass\n"
        "@pytest.mark.live\n"
        "def test_live(): pass\n"
        "@pytest.mark.eval\n"
        "def test_eval(): pass\n"
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"),
            "--rootdir", str(ROOT), "--no-cov", "-p", "no:cacheprovider",
            "--collect-only", "-q", str(tmp_path / "test_sample.py"),
        ],
        capture_output=True, text=True, cwd=tmp_path, check=False,
    )  # fmt: skip
    assert "test_plain" in result.stdout
    assert "test_live" not in result.stdout
    assert "test_eval" not in result.stdout
    assert "2 deselected" in result.stdout
