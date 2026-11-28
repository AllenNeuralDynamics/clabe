"""End to end: a run the daemon hosts with the terminal UI, shown by a window (`clabe daemon view`) that is typed
into and closed. The window itself runs in a pseudo console here, so its keys and its closing are real."""

import os
import sys
import threading
import time
from pathlib import Path

import pytest
import requests
import uvicorn

from clabe.daemon import DaemonSettings, Host, RepositoryConfig, _terminal, create_daemon_app
from clabe.server import free_port

pytestmark = pytest.mark.skipif(not _terminal.available(), reason="needs Windows and pywinpty")

_EXPERIMENT = """
from clabe import ui
from clabe.workflow import Workflow

demo = Workflow("demo", exit_prompt_timeout=0)


@demo.step
def subject() -> str:
    return ui.text("Subject name")


@demo.step
def trials(subject: str) -> float:
    return ui.number("Number of trials", minimum=1)
"""


@pytest.fixture
def daemon(tmp_path: Path, monkeypatch, end_launched_run):
    import subprocess

    repo = tmp_path / "Aind.Experiment.Demo"
    repo.mkdir()
    (repo / "main.py").write_text(_EXPERIMENT, encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    port = free_port("127.0.0.1", 18270)
    monkeypatch.setenv("DAEMON_CLABE_PORT", str(port))
    monkeypatch.setenv("DAEMON_CLABE_TOKEN", "d4emon")
    settings = DaemonSettings(
        port=port,
        repository_roots=[],
        repositories=[RepositoryConfig(path=repo)],
        command=[sys.executable, "-m", "clabe.cli"],
        run_arguments=["--allow-dirty", "--skip-hardware-validation"],
        runs_directory=tmp_path / "runs",
        stop_grace=20.0,
    )
    host = Host(settings)
    server = uvicorn.Server(
        uvicorn.Config(create_daemon_app(host, token="d4emon"), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", host
    end_launched_run(host)
    server.should_exit = True
    thread.join(5)


class _Window:
    """`clabe daemon view` in a pseudo console of its own: what it shows, the keys typed into it."""

    def __init__(self) -> None:
        from winpty import PtyProcess

        self.pty = PtyProcess.spawn(
            [sys.executable, "-m", "clabe", "daemon", "view"], env=dict(os.environ), dimensions=(30, 110)
        )
        self.text = ""
        threading.Thread(target=self._follow, daemon=True).start()

    def _follow(self) -> None:
        while True:
            try:
                self.text += self.pty.read(65536)
            except Exception:  # noqa: BLE001 -- closed
                return

    def close(self) -> None:
        """As the operator closing the window: its console goes, and Windows sends its process the close event."""
        import gc

        self.pty.fileobj.close()
        del self.pty.pty  # the pseudo console is closed when its last reference goes
        gc.collect()

    def shows(self, needle: str, timeout: float = 90.0) -> bool:
        deadline = time.monotonic() + timeout
        while needle not in self.text:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.1)
        return True


def _wait(condition, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def test_a_window_shows_the_run_is_typed_into_and_closing_it_stops_the_run(daemon):
    base, host = daemon
    launched = requests.post(
        f"{base}/api/daemon/runs",
        json={"repository": "Aind.Experiment.Demo", "frontend": "tui", "window": False},
        headers={"Authorization": "Bearer d4emon"},
        timeout=30,
    )
    assert launched.ok, launched.text
    assert launched.json()["terminal"]

    window = _Window()
    assert window.shows("Subject name"), window.text[-2000:]
    window.pty.write("mouse-7\r")
    assert window.shows("Number of trials"), window.text[-2000:]

    window.close()  # the operator closes the window: the run is stopped
    assert _wait(lambda: not host.busy()), "closing the window did not stop the run"
    assert host.current().exit_code() is not None


def test_the_terminal_ui_s_address_is_only_given_to_this_machine(daemon):
    from fastapi.testclient import TestClient

    _, host = daemon
    remote = TestClient(create_daemon_app(host, token="d4emon"), client=("10.0.0.9", 50000))
    answer = remote.get("/api/daemon/runs/current/terminal", headers={"Authorization": "Bearer d4emon"})
    assert answer.status_code == 403
    local = TestClient(create_daemon_app(host, token="d4emon"), client=("127.0.0.1", 50000))
    assert local.get("/api/daemon/runs/current/terminal", headers={"Authorization": "Bearer d4emon"}).status_code == 404


def _clabe_run(repository: Path) -> _Window:
    """`clabe run` at the rig (the desktop icon), in a console of its own."""
    window = _Window.__new__(_Window)
    from winpty import PtyProcess

    window.pty = PtyProcess.spawn(
        [sys.executable, "-m", "clabe", "run", "main.py"],
        cwd=str(repository),
        env=dict(os.environ),
        dimensions=(30, 110),
    )
    window.text = ""
    threading.Thread(target=window._follow, daemon=True).start()
    return window


def test_a_window_lost_at_the_rig_leaves_the_run_and_the_icon_resumes_it(daemon):
    from clabe.daemon._host import terminate_pid

    _, host = daemon
    repository = host.settings.repositories[0].path
    first = _clabe_run(repository)
    assert first.shows("Subject name"), first.text[-2000:]
    assert host.current().info().from_rig

    terminate_pid(first.pty.pid)  # the window dies without closing (killed): the run goes on
    time.sleep(1)
    assert host.busy()

    again = _clabe_run(repository)
    assert again.shows("A previous run was not closed"), again.text[-2000:]
    again.pty.write("r")
    assert again.shows("Subject name"), again.text[-2000:]
    again.pty.write("mouse-7\r")
    assert again.shows("Number of trials"), again.text[-2000:]
    again.close()
    assert _wait(lambda: not host.busy()), "closing the window did not stop the run"
