"""A run takes the processes it started with it; the few started to outlive it stay (Windows job objects)."""

import json
import subprocess
import sys
import textwrap
import time

import pytest

from clabe import _process
from clabe.daemon._host import pid_alive

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows'")

_SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


def _wait_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while pid_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def _written(path, timeout: float = 20.0) -> str:
    """What a script wrote to ``path``, once it is there (scripts write a temporary file, then rename it). A file
    just renamed can be locked for a moment on Windows (antivirus, indexing): reading is retried."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if text := path.read_text():
                return text
        except OSError:  # not there yet, or locked
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"Nothing written to {path}")
        time.sleep(0.1)


def _write(path) -> str:
    """Code for a script: ``write(text)`` puts ``text`` in ``path`` whole (a reader never sees it half written)."""
    return f"""
import os
def write(text):
    with open({str(path) + ".tmp"!r}, "w") as f:
        f.write(text)
    os.replace({str(path) + ".tmp"!r}, {str(path)!r})
"""


def _kill(*pids: int) -> None:
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)


def test_what_a_run_starts_ends_with_it_but_what_it_detaches(tmp_path):
    """The run (here a script calling `contain_children`) is killed: its child goes, its detached child stays."""
    out = tmp_path / "pids.json"
    script = _write(out) + textwrap.dedent(
        f"""
        import json, subprocess, sys, time
        from clabe._process import contain_children, start_detached
        assert contain_children() is not None
        child = subprocess.Popen({_SLEEP!r})
        detached = start_detached({_SLEEP!r})
        write(json.dumps([child.pid, detached.pid]))
        time.sleep(60)
        """
    )
    run = subprocess.Popen([sys.executable, "-c", script])
    pids: list[int] = []
    try:
        pids = json.loads(_written(out))
        child, detached = pids
        run.kill()  # as the Task Manager or a crash would
        assert _wait_dead(child), "the run's child outlived it"
        assert pid_alive(detached), "the detached process ended with the run"
    finally:
        run.kill()
        _kill(*pids)


def test_a_process_started_in_a_job_cannot_start_children_outside_it(tmp_path):
    """`popen_in_job` starts it suspended, in the job, before it runs: ending the job ends its children too."""
    out = tmp_path / "child.txt"
    script = (
        _write(out) + f"import subprocess, time; p = subprocess.Popen({_SLEEP!r}); write(str(p.pid)); time.sleep(60)"
    )
    job = _process.Job(kill_on_close=False)
    parent = _process.popen_in_job(job, [sys.executable, "-c", script])
    try:
        child = int(_written(out))
        assert job.contains(parent.pid) and job.contains(child)
        job.terminate()
        assert parent.wait(10) is not None and _wait_dead(child)
    finally:
        parent.kill()
        job.close()


def test_a_job_that_does_not_end_with_its_owner_leaves_its_processes_when_let_go():
    job = _process.Job(kill_on_close=False)
    process = _process.popen_in_job(job, _SLEEP)
    try:
        job.close()  # the daemon restarting: its runs go on
        time.sleep(0.5)
        assert process.poll() is None
    finally:
        process.kill()


def test_detached_apps_use_start_detached(monkeypatch):
    from clabe.apps import LocalDetachedExecutor
    from clabe.apps._base import Command, identity_parser

    started = []
    monkeypatch.setattr("clabe.apps._executors.start_detached", lambda cmd, **kw: started.append((cmd, kw)))
    LocalDetachedExecutor(cwd="C:/somewhere").run(Command(cmd=["waterlog.exe"], output_parser=identity_parser))
    assert started == [(["waterlog.exe"], {"cwd": "C:/somewhere", "env": None, "shell": False})]
