"""The installer (scripts/install.ps1), dry-run: what it would run, however it is invoked, and what it decides."""

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
    for name in ("DAEMON_CLABE_VERSION", "DAEMON_CLABE_PACKAGE", "DAEMON_CLABE_FORCE", "DAEMON_CLABE_REINSTALL"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _installed(tmp_path: Path, version: str, extras: list[str], direct_url: dict | None = None) -> None:
    """A uv tool environment with aind-clabe ``version`` (its metadata only) and uv's receipt."""
    tool = tmp_path / "tools" / "aind-clabe"
    subprocess.run(["uv", "venv", "--quiet", str(tool)], check=True)
    info = tool / "Lib" / "site-packages" / f"aind_clabe-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: aind-clabe\nVersion: {version}\n")
    if direct_url:
        (info / "direct_url.json").write_text(json.dumps(direct_url))
    listed = ", ".join(f'"{e}"' for e in extras)
    (tool / "uv-receipt.toml").write_text(f'[tool]\nrequirements = [{{ name = "aind-clabe", extras = [{listed}] }}]\n')


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


def _decision(lines: list[str]) -> str:
    return next(line for line in lines if line.startswith("==> Decision:"))


def test_as_a_file_with_arguments():
    lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-Token", "t0k", "-DryRun")
    commands = _commands(lines)
    assert commands[0] == "schtasks /End /TN clabe-daemon"
    assert commands[1].endswith("tool install --force aind-clabe[frontend,otel]==0.18.0")
    assert commands[2].endswith("clabe.exe daemon install")
    assert commands[3].endswith("clabe.exe daemon start --timeout 120")
    assert "t0k" not in "\n".join(lines)  # the token is never printed


def test_piped_into_iex_with_environment_variables():
    """What `irm <url> | iex` does: no arguments, options from DAEMON_CLABE_* variables."""
    env = {"DAEMON_CLABE_VERSION": "0.18.0", "DAEMON_CLABE_FORCE": "1", "DAEMON_CLABE_DRY_RUN": "1"}
    commands = _commands(_powershell("-Command", f"Get-Content -Raw '{SCRIPT}' | Invoke-Expression", env=env))
    assert commands[1].endswith("tool install --force aind-clabe[frontend,otel]==0.18.0")


def test_as_a_script_block_with_arguments():
    command = f"& ([scriptblock]::Create((Get-Content -Raw '{SCRIPT}'))) -Package 'aind-clabe[agents]==0.18.0' -DryRun"
    assert _commands(_powershell("-Command", command))[1].endswith("tool install --force aind-clabe[agents]==0.18.0")


def test_a_failure_is_a_non_zero_exit_and_installs_nothing():
    done = _run(
        "-Command", f"Get-Content -Raw '{SCRIPT}' | iex", env={"DAEMON_CLABE_CONFIG": "C:/does/not/exist/clabe.yml"}
    )
    assert done.returncode != 0 and "Installing aind-clabe" not in done.stdout


def test_the_configuration_can_come_from_standard_input():
    """Over SSH: ssh rig "powershell ... -Config -" < clabe.yml (no file to copy first)."""
    command = f"& ([scriptblock]::Create((Get-Content -Raw '{SCRIPT}'))) -Config - -DryRun"
    config = "daemon:\n  repository_roots: [D:/git]\n"
    lines = _powershell("-Command", command, stdin=config)
    assert any("from standard input" in line for line in lines)
    assert "repository_roots: [D:/git]" in lines


def test_without_uv_it_stops_before_doing_anything(tmp_path):
    env = {"PATH": os.environ.get("SystemRoot", "C:/Windows") + "/System32", "USERPROFILE": str(tmp_path)}
    done = _run("-File", str(SCRIPT), "-DryRun", env=env)
    assert done.returncode != 0 and "uv is not installed" in done.stderr + done.stdout
    assert "Installing" not in done.stdout


class TestDecision:
    """Restart only, or stop, install and start (on Windows the running daemon locks the files uv replaces)."""

    def test_nothing_installed(self):
        lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-DryRun")
        assert _decision(lines) == "==> Decision: stop, install, start (clabe is not installed)"

    def test_the_same_version_only_restarts(self, _isolated):
        _installed(_isolated, "0.18.0", ["frontend", "otel"])
        lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-DryRun")
        assert _decision(lines) == "==> Decision: restart only (clabe 0.18.0 is already installed)"
        commands = _commands(lines)
        assert not any("tool install" in c for c in commands)
        assert commands[0].endswith("daemon install") and commands[1].endswith("daemon restart --timeout 120")

    def test_the_same_version_with_reinstall_installs(self, _isolated):
        _installed(_isolated, "0.18.0", ["frontend", "otel"])
        lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-Reinstall", "-DryRun")
        assert _decision(lines).startswith("==> Decision: stop, install, start (-Reinstall")

    def test_other_extras_install(self, _isolated):
        _installed(_isolated, "0.18.0", ["frontend"])
        lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-DryRun")
        assert _decision(lines).startswith("==> Decision: stop, install, start")

    def test_a_different_version_stops_installs_and_starts_with_a_rollback(self, _isolated):
        _installed(_isolated, "0.17.0", ["frontend", "otel"])
        lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-DryRun")
        assert _decision(lines) == (
            "==> Decision: stop, install, start "
            "(clabe 0.17.0 is installed; aind-clabe[frontend,otel]==0.18.0 is requested)"
        )
        assert "==> To restore if the install fails: aind-clabe[frontend,otel]==0.17.0" in lines
        commands = _commands(lines)
        assert commands[0] == "schtasks /End /TN clabe-daemon"  # this clabe has no `clabe daemon stop`
        assert commands[1].endswith("tool install --force aind-clabe[frontend,otel]==0.18.0")

    def test_the_same_git_commit_only_restarts(self, _isolated):
        commit = "c7cc84fb0ad11698d62a351aff28fd6d2b2e7600"
        url = "https://github.com/bruno-f-cruz/clabe"
        vcs = {"url": url, "vcs_info": {"vcs": "git", "commit_id": commit}}
        _installed(_isolated, "0.19.0", ["frontend", "otel"], vcs)
        same = f"aind-clabe[frontend,otel] @ git+{url}@{commit}"
        assert "restart only" in _decision(_powershell("-File", str(SCRIPT), "-Package", same, "-DryRun"))
        lines = _powershell("-File", str(SCRIPT), "-Package", same.replace("c7cc84fb", "00000000"), "-DryRun")
        assert "stop, install, start" in _decision(lines)
        assert f"==> To restore if the install fails: {same}" in lines

    def test_an_unpinned_request_installs(self, _isolated):
        _installed(_isolated, "0.18.0", ["frontend", "otel"])
        assert "does not pin a version" in _decision(_powershell("-File", str(SCRIPT), "-DryRun"))

    @pytest.mark.parametrize("status", ["busy", "leased"])
    def test_a_busy_daemon_stops_it_before_anything_changes(self, _isolated, daemon_answering, status):
        daemon_answering.update(status=status, leased_by="rig-1")
        _installed(_isolated, "0.17.0", ["frontend", "otel"])
        done = _run("-File", str(SCRIPT), "-Version", "0.18.0", "-Token", "t0k", "-DryRun")
        assert done.returncode != 0 and "would interrupt it" in done.stderr + done.stdout
        assert "Decision" not in done.stdout and "DAEMON_CLABE_TOKEN" not in done.stdout

    def test_a_busy_daemon_is_interrupted_with_force(self, daemon_answering):
        daemon_answering.update(status="busy")
        lines = _powershell("-File", str(SCRIPT), "-Version", "0.18.0", "-Force", "-DryRun")
        assert "==> The daemon runs an experiment; -Force: it is interrupted." in lines
        assert "stop, install, start" in _decision(lines)
