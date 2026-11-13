import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_tdd.sh"
TEST_FILE = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "stage/01-demo")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text(TEST_FILE)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "red")
    git(tmp_path, "tag", "stage-01-red")
    return tmp_path


def run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True, check=False
    )


def commit_change(repo: Path, content: str) -> None:
    (repo / "tests" / "test_demo.py").write_text(content)
    git(repo, "commit", "-q", "-am", "change")


def test_tdd_guard_passes_when_tests_intact(repo: Path) -> None:
    commit_change(repo, TEST_FILE + "\n\ndef test_three():\n    assert True\n")
    assert run_guard(repo).returncode == 0


def test_tdd_guard_fails_when_initial_test_deleted(repo: Path) -> None:
    commit_change(repo, "def test_one():\n    assert True\n")
    result = run_guard(repo)
    assert result.returncode != 0
    assert "test_two" in result.stdout + result.stderr


def test_tdd_guard_fails_when_initial_test_skipped(repo: Path) -> None:
    skipped = TEST_FILE.replace(
        "def test_two", "import pytest\n\n\n@pytest.mark.skip\ndef test_two"
    )
    commit_change(repo, skipped)
    result = run_guard(repo)
    assert result.returncode != 0
    assert "test_two" in result.stdout + result.stderr
