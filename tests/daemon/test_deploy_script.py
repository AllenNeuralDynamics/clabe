"""The installer (scripts/install.ps1), dry-run: what it would run, however it is invoked."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None or shutil.which("uv") is None,
    reason="the installer targets Windows rig PCs with uv",
)


def _powershell(*args: str, env: dict[str, str] | None = None) -> list[str]:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, **(env or {})},
    )
    assert done.returncode == 0, done.stderr
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _commands(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith("==>")]


def test_as_a_file_with_arguments():
    lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-Token", "t0k", "-DryRun")
    commands = _commands(lines)
    assert commands[0].endswith("tool install --force aind-clabe==0.18.0")
    assert commands[1].endswith("clabe.exe daemon install")
    assert commands[2].endswith("clabe.exe daemon restart --timeout 120")
    assert "t0k" not in "\n".join(lines)  # the token is never printed


def test_piped_into_iex_with_environment_variables():
    """What `irm <url> | iex` does: no arguments, options from CLABE_* variables."""
    env = {"CLABE_VERSION": "0.18.0", "CLABE_FORCE": "1", "CLABE_DRY_RUN": "1"}
    commands = _commands(_powershell("-Command", f"Get-Content -Raw '{SCRIPT}' | Invoke-Expression", env=env))
    assert commands[0].endswith("tool install --force aind-clabe==0.18.0")
    assert commands[2].endswith("daemon restart --timeout 120 --force")


def test_as_a_script_block_with_arguments():
    command = f"& ([scriptblock]::Create((Get-Content -Raw '{SCRIPT}'))) -Package 'aind-clabe[agents]==0.18.0' -DryRun"
    assert _commands(_powershell("-Command", command))[0].endswith("tool install --force aind-clabe[agents]==0.18.0")


def test_a_failure_is_a_non_zero_exit_and_installs_nothing():
    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", f"Get-Content -Raw '{SCRIPT}' | iex"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "CLABE_CONFIG": "C:/does/not/exist/clabe.yml"},
    )
    assert done.returncode != 0 and "Installing aind-clabe" not in done.stdout
