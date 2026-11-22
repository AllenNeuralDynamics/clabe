"""The deployment script (scripts/deploy/Deploy-ClabeDaemon.ps1), dry-run: what it would run, in which order."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy" / "Deploy-ClabeDaemon.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None or shutil.which("uv") is None,
    reason="the deployment script targets Windows rig PCs with uv",
)


def _dry_run(*args: str) -> list[str]:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args, "-DryRun"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def test_it_installs_a_tool_registers_the_task_and_restarts():
    lines = _dry_run("-Package", "aind-clabe==0.18.0", "-Token", "t0k")
    commands = [line for line in lines if not line.startswith("==>")]
    assert commands[0].endswith("tool install --force aind-clabe==0.18.0")
    assert commands[1].endswith("clabe.exe daemon install")
    assert commands[2].endswith("clabe.exe daemon restart --timeout 120")
    assert "t0k" not in "\n".join(lines)  # the token is never printed


def test_with_uvx_the_task_runs_the_pinned_package():
    lines = _dry_run("-Package", "aind-clabe==0.18.0", "-Uvx", "-Force")
    assert any(line.endswith("daemon install --uvx aind-clabe==0.18.0") for line in lines)
    assert any(line.endswith("daemon restart --timeout 120 --force") for line in lines)
