"""fix/bot-control: локальный запуск — scripts/run_local.sh, compose.local.yaml, код выхода 3."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from xcopilot import main as main_module

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "run_local.sh"


def test_restart_exit_code_is_three() -> None:
    assert main_module.RESTART_EXIT_CODE == 3


def test_run_exits_with_restart_code_when_restart_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:t")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    async def fake_main() -> bool:
        return True  # владелец нажал «Перезапустить»

    monkeypatch.setattr(main_module, "_main", fake_main)
    with pytest.raises(SystemExit) as exc:
        main_module.run()
    assert exc.value.code == 3


def test_compose_local_publishes_db_on_loopback_only() -> None:
    data = yaml.safe_load((ROOT / "compose.local.yaml").read_text())
    ports = data["services"]["db"]["ports"]
    assert ports == ["127.0.0.1:5433:5432"]


def test_logs_dir_is_ignored() -> None:
    assert "logs/" in (ROOT / ".gitignore").read_text().splitlines()


def test_prod_compose_restarts_on_any_exit_code() -> None:
    for name in ("compose.yaml", "compose.prod.yaml"):
        services = yaml.safe_load((ROOT / name).read_text())["services"]
        assert services["app"]["restart"] == "unless-stopped"  # код 3 — тоже перезапуск


def test_script_is_executable_and_valid_bash() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def _stand(
    tmp_path: Path, codes: list[int], env_file: str | None = None
) -> tuple[Path, dict[str, str]]:
    """Копия скрипта в пустой папке и заглушка `uv`, которая отвечает заданными кодами."""
    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "run_local.sh")
    if env_file is not None:
        (root / ".env").write_text(env_file)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    counter = tmp_path / "n"
    counter.write_text("0")
    codes_s = " ".join(str(c) for c in codes)
    stub = bin_dir / "uv"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$* DB=$DATABASE_URL" >> "{log}"\n'
        'case "$*" in *db.migrate*) exit 0;; esac\n'
        f'n=$(cat "{counter}"); echo $((n+1)) > "{counter}"\n'
        f"codes=({codes_s})\n"
        'exit "${codes[$n]:-0}"\n'
    )
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUN_LOCAL_PAUSE": "0",
        "DATABASE_URL": "",
    }
    env.pop("DATABASE_URL")
    return root, env


def _run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "run_local.sh"), *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )  # fmt: skip


def test_script_restarts_on_code_3_and_on_crash_then_exits_on_clean_stop(tmp_path: Path) -> None:
    root, env = _stand(
        tmp_path, [3, 1, 0],
        "BOT_TOKEN=x\nDATABASE_URL=postgresql+asyncpg://u:p@db:5432/xcopilot\n",
    )  # fmt: skip
    done = _run(root, env)
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    mains = [c for c in calls if "xcopilot.main" in c]
    assert len(mains) == 3  # код 3 → снова; падение (1) → снова; 0 → конец
    assert sum("db.migrate" in c for c in calls) >= 1  # миграции перед запуском
    assert all("@127.0.0.1:5433/xcopilot" in c for c in calls)  # БД с хоста, не db:5432
    assert "u:p@" in calls[0]  # логин и пароль из .env сохранены
    assert (root / "logs" / "bot.log").exists()


def test_script_works_without_env_file_with_default_db(tmp_path: Path) -> None:
    root, env = _stand(tmp_path, [0])
    assert _run(root, env).returncode == 0
    calls = (tmp_path / "calls.log").read_text()
    assert "127.0.0.1:5433" in calls
