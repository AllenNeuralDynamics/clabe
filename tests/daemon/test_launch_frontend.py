"""What a run launched by the daemon shows on the rig: nothing, the terminal UI, or the browser."""

import subprocess
import sys

import pytest

from clabe.daemon import DaemonSettings, Host, RepositoryConfig
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


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A host whose launches are recorded, not started."""
    (tmp_path / "repo").mkdir()
    settings = DaemonSettings(
        repository_roots=[], repositories=[RepositoryConfig(path=tmp_path / "repo")], runs_directory=tmp_path / "runs"
    )
    launched = []

    class Process:
        pid = 4242

        def poll(self):
            return None

    def popen(command, **options):
        launched.append(options)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)
    return Host(settings), launched


@pytest.mark.skipif(sys.platform != "win32", reason="the terminal UI opens a Windows console")
def test_the_terminal_ui_gets_a_console_window_of_its_own(host):
    host, launched = host
    host.launch(LaunchBody(repository="repo", frontend="tui"))
    assert launched[0]["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert "stdout" not in launched[0]  # its output is in its window


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creation flags")
def test_otherwise_the_run_has_no_window_and_logs_to_its_file(host):
    host, launched = host
    host.launch(LaunchBody(repository="repo", frontend="browser"))
    assert launched[0]["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert launched[0]["stdout"] is not None


@pytest.mark.skipif(sys.platform == "win32", reason="elsewhere than Windows")
def test_the_terminal_ui_needs_windows(host):
    host, _ = host
    with pytest.raises(HostError, match="Windows"):
        host.launch(LaunchBody(repository="repo", frontend="tui"))
