"""``POST /api/daemon/restart``: the daemon ends itself and its task starts a new one."""

import pytest
from fastapi.testclient import TestClient

from clabe.daemon import DaemonSettings, Host, _install, _watchdog, create_daemon_app


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    """The daemon's app with its status set by the test; restarting and ending are recorded, not done."""
    host = Host(DaemonSettings(repository_roots=[], runs_directory=tmp_path / "runs"))
    state = {"status": "idle", "calls": []}
    real_info = host.info
    monkeypatch.setattr(host, "info", lambda: real_info().model_copy(update={"status": state["status"]}))
    monkeypatch.setattr(host, "stop", lambda: state["calls"].append("stop run"))
    monkeypatch.setattr(_install, "restart_later", lambda pid: state["calls"].append("restart later"))
    monkeypatch.setattr(_watchdog, "end_process", lambda code: state["calls"].append(f"end {code}"))
    with TestClient(create_daemon_app(host, token="t0ken", static_dir=None)) as client:
        client.headers["Authorization"] = "Bearer t0ken"
        yield client, state


def test_it_answers_then_ends_itself_and_its_task_starts_a_new_one(daemon):
    client, state = daemon
    answer = client.post("/api/daemon/restart")
    assert answer.status_code == 202 and "restarts" in answer.json()["detail"]
    assert state["calls"] == ["restart later", "end 0"]  # the start is arranged before it ends


@pytest.mark.parametrize("status", ["busy", "leased"])
def test_a_session_is_not_interrupted_unless_forced(daemon, status):
    client, state = daemon
    state["status"] = status
    answer = client.post("/api/daemon/restart")
    assert answer.status_code == 409 and "interrupt" in answer.json()["detail"]
    assert state["calls"] == []
    assert client.post("/api/daemon/restart", params={"force": True}).status_code == 202
    assert state["calls"] == (["stop run"] if status == "busy" else []) + ["restart later", "end 0"]


def test_a_daemon_its_task_did_not_start_says_so_and_keeps_running(daemon, monkeypatch):
    client, state = daemon

    def refuse(pid):
        raise OSError("The clabe-daemon task is not installed")

    monkeypatch.setattr(_install, "restart_later", refuse)
    answer = client.post("/api/daemon/restart")
    assert answer.status_code == 409 and "not installed" in answer.json()["detail"]
    assert state["calls"] == []


def test_it_needs_the_token(daemon):
    client, _ = daemon
    assert client.post("/api/daemon/restart", headers={"Authorization": ""}).status_code == 401


def test_the_helper_waits_for_the_daemon_and_its_task_before_starting_it():
    script = _install._restart_script(4242)
    assert script.index("Wait-Process -Id 4242") < script.index("-eq 'Running'") < script.index("Start-ScheduledTask")
    assert "Enable-ScheduledTask -TaskName 'clabe-daemon'" in script
