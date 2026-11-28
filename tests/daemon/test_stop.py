"""Stopping the daemon's run: it is asked to abort and close; one that does not is ended with all it started. A
restarted daemon finds its run again."""

import subprocess
import sys
import textwrap
import time

import pytest

from clabe.daemon import DaemonSettings, Host, RepositoryConfig
from clabe.daemon._host import LaunchBody, pid_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="runs end with what they started through job objects")

#: A stand-in for `clabe run`: it starts a child (as Bonsai), and, when ABORTABLE, serves /api/abort and /api/close
#: (closing ends it), announcing its port as a run server does.
_RUN = textwrap.dedent(
    """
    import http.server, json, os, subprocess, sys, time
    from clabe._process import contain_children

    contain_children()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(os.environ["CHILD_FILE"], "w").write(str(child.pid))
    if os.environ.get("ABORTABLE") != "1":
        time.sleep(60)
    asked = []

    class Run(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            asked.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            if self.path == "/api/close" and "/api/abort" in asked:
                os._exit(0)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Run)
    with open(os.environ["CLABE_SERVER_ANNOUNCE"], "w") as announce:
        json.dump({"port": server.server_port, "pid": os.getpid()}, announce)
    server.serve_forever()
    """
)


def _wait(condition, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


@pytest.fixture
def settings(tmp_path, monkeypatch):
    (tmp_path / "repo").mkdir()
    script = tmp_path / "run.py"
    script.write_text(_RUN, encoding="utf-8")
    monkeypatch.setenv("CHILD_FILE", str(tmp_path / "child.txt"))
    return DaemonSettings(
        repository_roots=[],
        repositories=[RepositoryConfig(path=tmp_path / "repo")],
        runs_directory=tmp_path / "runs",
        command=[sys.executable, str(script)],
        stop_grace=15.0,
    )


def _launch(host: Host, tmp_path) -> int:
    """Launches the stand-in run; its child's pid."""
    host.launch(LaunchBody(repository="repo"))
    child_file = tmp_path / "child.txt"
    assert _wait(lambda: child_file.is_file() and child_file.read_text())
    return int(child_file.read_text())


def test_a_run_that_aborts_and_closes_ends_on_its_own(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("ABORTABLE", "1")
    host = Host(settings)
    child = _launch(host, tmp_path)
    assert _wait(lambda: host.current().poll_port() is not None)
    started = time.monotonic()
    host.stop(wait=True)
    assert time.monotonic() - started < 10  # it closed: it was not waited out
    assert host.current().exit_code() == 0  # it exited, it was not killed
    assert _wait(lambda: not pid_alive(child))  # and what it started went with it


def test_a_run_that_does_not_abort_is_ended_with_what_it_started(settings, tmp_path):
    host = Host(settings.model_copy(update={"stop_grace": 1.0}))
    child = _launch(host, tmp_path)
    host.stop(wait=True)
    assert not host.busy()
    assert _wait(lambda: not pid_alive(child))


def test_a_restarted_daemon_finds_its_run_again_and_can_stop_it(settings, tmp_path):
    first = Host(settings)
    child = _launch(first, tmp_path)
    pid = first.current().pid

    restarted = Host(settings.model_copy(update={"stop_grace": 1.0}))  # a new daemon, as after a restart
    assert restarted.busy() and restarted.current().pid == pid
    assert restarted.info().status == "busy"
    restarted.stop(wait=True)
    assert _wait(lambda: not pid_alive(pid)) and _wait(lambda: not pid_alive(child))


def test_a_run_that_ended_is_not_found_again(settings, tmp_path):
    first = Host(settings.model_copy(update={"stop_grace": 1.0}))
    _launch(first, tmp_path)
    first.stop(wait=True)
    assert not Host(settings).busy()


def test_stopping_answers_at_once_unless_asked_to_wait(settings, tmp_path):
    host = Host(settings.model_copy(update={"stop_grace": 3.0}))
    child = _launch(host, tmp_path)
    started = time.monotonic()
    host.stop(wait=False)
    assert time.monotonic() - started < 2
    assert _wait(lambda: not host.busy()) and _wait(lambda: not pid_alive(child))


def test_nothing_to_stop(settings):
    from clabe.daemon._host import HostError

    with pytest.raises(HostError, match="No run"):
        Host(settings).stop()


def test_the_daemon_s_job_does_not_end_its_runs_when_it_goes(settings, tmp_path):
    """A daemon that ends (a restart) lets go of its runs' jobs: the runs go on."""
    host = Host(settings)
    _launch(host, tmp_path)
    run = host.current()
    run.job.close()
    time.sleep(0.5)
    assert run.alive()
    subprocess.run(["taskkill", "/PID", str(run.pid), "/T", "/F"], capture_output=True, check=False)
