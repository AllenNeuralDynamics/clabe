"""What a run launched by the daemon shows on the rig: nothing, the terminal UI, or the browser."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from clabe.daemon import DaemonSettings, Host, RepositoryConfig
from clabe.daemon import _host as host_module
from clabe.daemon._host import HostError, LaunchBody, run_arguments


@pytest.mark.parametrize(
    "frontend, shown, extra",
    [("none", "headless", False), ("browser", "headless", True), ("tui", "tui", False)],
)
def test_the_run_s_arguments(frontend, shown, extra):
    args = run_arguments(LaunchBody(repository="r", frontend=frontend, experiment="demo"), 18000, "t0k")
    assert args[:4] == ["run", "main.py", "--frontend", shown]
    assert "--serve" in args and "--token=t0k" in args and args[-2:] == ["--experiment-name", "demo"]
    assert ("--open-browser" in args) is extra


def test_more_arguments_come_last():
    args = run_arguments(LaunchBody(repository="r", arguments=["--debug-mode"]), 18000, "t0k")
    assert args[-1] == "--debug-mode"


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host whose launches are recorded, not started."""
    (tmp_path / "repo").mkdir()
    settings = DaemonSettings(
        repository_roots=[], repositories=[RepositoryConfig(path=tmp_path / "repo")], runs_directory=tmp_path / "runs"
    )
    launched = []
    windows = []

    class Process:
        pid = 4242

        def poll(self):
            return None

    def popen_in_job(job, command, **options):
        launched.append((command, options))
        return Process()

    monkeypatch.setattr(host_module, "popen_in_job", popen_in_job)
    monkeypatch.setattr(host_module, "open_window", lambda: windows.append(True))
    monkeypatch.setattr(host_module._terminal, "available", lambda: sys.platform == "win32")
    return Host(settings), launched, windows


@pytest.mark.skipif(sys.platform != "win32", reason="the terminal UI is hosted in a Windows pseudo console")
def test_the_terminal_ui_runs_under_a_terminal_host_and_a_window_shows_it(host):
    host, launched, windows = host
    host.launch(LaunchBody(repository="repo", frontend="tui"))
    command, options = launched[0]
    assert command[1:3] == ["-m", "clabe.daemon._terminal"]
    spec = json.loads(Path(command[3]).read_text(encoding="utf-8"))
    assert spec["argv"][-len(run_arguments(LaunchBody(repository="r", frontend="tui"), 1, "t")) :][0] == "run"
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW  # the host has no window: the run's is a window
    assert windows == [True]
    assert host.current().info().terminal


@pytest.mark.skipif(sys.platform != "win32", reason="the terminal UI is hosted in a Windows pseudo console")
def test_whoever_launched_may_show_it_instead(host):
    host, _, windows = host
    host.launch(LaunchBody(repository="repo", frontend="tui", window=False, from_rig=True))
    assert windows == []
    assert host.current().info().from_rig


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation flags")
def test_otherwise_the_run_has_no_window_and_logs_to_its_file(host):
    host, launched, windows = host
    host.launch(LaunchBody(repository="repo", frontend="browser"))
    command, options = launched[0]
    assert "clabe.daemon._terminal" not in command
    assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert options["stdout"] is not None
    assert windows == [] and not host.current().info().terminal


@pytest.mark.skipif(sys.platform == "win32", reason="elsewhere than Windows")
def test_the_terminal_ui_needs_windows(host):
    host, _, _ = host
    with pytest.raises(HostError, match="Windows"):
        host.launch(LaunchBody(repository="repo", frontend="tui"))
