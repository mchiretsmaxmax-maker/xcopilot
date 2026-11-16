"""feat/device-transfer: режим на устройстве — compose, шаблоны службы, setup.sh --check."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
TEMPLATES = SCRIPTS / "templates"
SECRET = "123456:SECRET-bot-token"


def test_host_compose_publishes_db_only_on_loopback() -> None:
    # compose.host.yaml не нужен: режим на устройстве = compose.yaml + compose.local.yaml
    data = yaml.safe_load((ROOT / "compose.local.yaml").read_text())
    assert list(data["services"]) == ["db"]
    for port in data["services"]["db"]["ports"]:
        assert port.startswith("127.0.0.1:")


@pytest.mark.parametrize(
    "script", ["setup.sh", "service.sh", "transfer-export.sh", "transfer-import.sh"]
)
def test_scripts_are_executable_and_valid_bash(script: str) -> None:
    path = SCRIPTS / script
    assert path.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(path)], check=True)


def _render(name: str) -> str:
    text = (TEMPLATES / name).read_text()
    values = {
        "WORKDIR": "/opt/xc repo",
        "PATH_DIRS": "/opt/uv/bin:/opt/claude/bin:/usr/bin",
        "LOG_DIR": "/var/log/xcopilot",
    }
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    assert "{{" not in text, "нераскрытый плейсхолдер"
    return text


def test_service_templates_render() -> None:
    plist = _render("com.xcopilot.bot.plist")
    unit = _render("xcopilot.service")
    for text in (plist, unit):
        assert "/opt/xc repo" in text
        assert "scripts/run_local.sh" in text
        assert "--daemon" not in text  # служба сама держит процесс
        assert "/opt/uv/bin:/opt/claude/bin" in text
        assert "/var/log/xcopilot" in text
        assert SECRET not in text and "BOT_TOKEN" not in text and "SECRETS_KEY" not in text
    assert "<key>KeepAlive</key>" in plist and "<key>RunAtLoad</key>" in plist
    assert "caffeinate" in plist  # Mac не засыпает, пока бот работает
    assert "Restart=always" in unit and "WantedBy=default.target" in unit


def test_setup_check_reports_missing_tools(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    for tool in ("docker", "uv", "openssl"):  # claude намеренно нет
        stub = fake / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPTS / "setup.sh"), "--check"],
        env={"PATH": str(fake), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "нет Claude CLI" in result.stdout + result.stderr
