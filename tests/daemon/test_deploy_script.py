"""The installer (scripts/install.ps1), dry-run: what it would run, however it is invoked."""

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from clabe.server import free_port

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None or shutil.which("uv") is None,
    reason="the installer targets Windows rig PCs with uv",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing installed and no daemon: this PC's own clabe and daemon never decide what the installer does."""
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("DAEMON_CLABE_PORT", str(free_port("127.0.0.1", 24900)))
    for name in (
        "DAEMON_CLABE_VERSION",
        "DAEMON_CLABE_LOCK",
        "DAEMON_CLABE_PACKAGE",
        "DAEMON_CLABE_FORCE",
        "DAEMON_CLABE_DEPLOY_HUBS",
        "DAEMON_CLABE_CLI_PACKAGE",
        "DAEMON_CLABE_STOP",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def daemon_answering():
    """A daemon on DAEMON_CLABE_PORT answering /api/daemon/info with the status the test sets."""
    info = {"status": "idle"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(info).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["DAEMON_CLABE_PORT"])), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield info
    server.shutdown()


def _run(*args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, **(env or {})},
    )


def _powershell(*args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> list[str]:
    done = _run(*args, env=env, stdin=stdin)
    assert done.returncode == 0, done.stderr
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _commands(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith("==>")]


def test_as_a_file_with_arguments():
    lines = _powershell(
        "-File", str(SCRIPT), "-Version", "0.18.0", "-Hubs", "http://a/;http://b/", "-Token", "t0k", "-DryRun"
    )
    commands = _commands(lines)
    assert commands[0].endswith("tool install --upgrade --force aind-clabe[frontend,otel]>=0.18.0rc2")
    assert commands[1].endswith("tool update-shell")
    assert commands[2].endswith("clabe.exe daemon deploy --timeout 120 --version 0.18.0 --hubs http://a/;http://b/")
    assert commands[3].endswith("clabe.exe daemon status")
    assert "t0k" not in "\n".join(lines)  # the token is never printed


def test_piped_into_iex_with_environment_variables():
    """What `irm <url> | iex` does: no arguments, options from DAEMON_CLABE_* variables."""
    env = {"DAEMON_CLABE_LOCK": "C:/git/Aind.Experiment.X", "DAEMON_CLABE_FORCE": "1", "DAEMON_CLABE_DRY_RUN": "1"}
    commands = _commands(_powershell("-Command", f"Get-Content -Raw '{SCRIPT}' | Invoke-Expression", env=env))
    assert commands[0].endswith("tool install --upgrade --force aind-clabe[frontend,otel]>=0.18.0rc2")
    assert commands[2].endswith("daemon deploy --timeout 120 --lock C:/git/Aind.Experiment.X --force")


def test_as_a_script_block_with_arguments():
    command = (
        f"& ([scriptblock]::Create((Get-Content -Raw '{SCRIPT}'))) "
        "-Package 'aind-clabe[agents]==0.18.0' -CliPackage 'aind-clabe==0.18.0' -DryRun"
    )
    commands = _commands(_powershell("-Command", command))
    assert commands[0].endswith("tool install --upgrade --force aind-clabe==0.18.0")
    assert commands[2].endswith("daemon deploy --timeout 120 --package aind-clabe[agents]==0.18.0")


def test_stop_daemon_only_installs_the_command_and_stops_it():
    commands = _commands(_powershell("-File", str(SCRIPT), "-StopDaemon", "-Force", "-DryRun"))
    assert commands[-1].endswith("clabe.exe daemon stop --if-installed --force")
    assert not any("deploy" in c for c in commands)


def test_a_failure_is_a_non_zero_exit_and_installs_nothing():
    done = _run(
        "-Command", f"Get-Content -Raw '{SCRIPT}' | iex", env={"DAEMON_CLABE_CONFIG": "C:/does/not/exist/clabe.yml"}
    )
    assert done.returncode != 0 and "Installing the clabe command" not in done.stdout


def test_the_configuration_can_come_from_standard_input():
    """Over SSH: ssh rig "powershell ... -Config -" < clabe.yml (no file to copy first)."""
    command = f"& ([scriptblock]::Create((Get-Content -Raw '{SCRIPT}'))) -Config - -DryRun"
    config = "daemon:\n  repository_roots: [D:/git]\n"
    lines = _powershell("-Command", command, stdin=config)
    assert any("from standard input" in line for line in lines)
    assert "repository_roots: [D:/git]" in lines


def test_without_uv_it_installs_uv_first(tmp_path):
    system = os.environ.get("SystemRoot", "C:/Windows") + "/System32"
    env = {"PATH": f"{system};{system}/WindowsPowerShell/v1.0", "USERPROFILE": str(tmp_path)}
    lines = _powershell("-File", str(SCRIPT), "-DryRun", env=env)
    assert lines[0] == f"==> Installing uv for {os.environ.get('USERNAME')}"
    assert "irm https://astral.sh/uv/install.ps1 | iex" in lines


@pytest.mark.parametrize("status", ["busy", "leased"])
def test_a_busy_daemon_stops_it_before_anything_changes(daemon_answering, status):
    daemon_answering.update(status=status, leased_by="rig-1")
    done = _run("-File", str(SCRIPT), "-Version", "0.18.0", "-Token", "t0k", "-DryRun")
    assert done.returncode != 0 and "would interrupt it" in done.stderr + done.stdout
    assert "DAEMON_CLABE_TOKEN" not in done.stdout and "Installing" not in done.stdout


def test_a_busy_daemon_is_interrupted_with_force(daemon_answering):
    daemon_answering.update(status="busy")
    commands = _commands(_powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-Force", "-DryRun"))
    assert commands[2].endswith("--version 0.18.0 --force")
