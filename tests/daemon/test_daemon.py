"""The daemon end to end: a repository, a launched run, and the run driven through the daemon's proxy."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests
import uvicorn

from clabe.daemon import DaemonSettings, Host, RepositoryConfig, _install, create_daemon_app
from clabe.server import RunClient, free_port
from clabe.workflow.events import PromptOpened

_EXPERIMENT = """
from clabe import ui
from clabe.workflow import Workflow

demo = Workflow("demo", exit_prompt_timeout=0)


@demo.step
def subject() -> str:
    return ui.text("Subject")


@demo.step
def trials(subject: str) -> float:
    return ui.number("Trials", minimum=1)
"""


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "Aind.Experiment.Demo"
    (repo / "experiments").mkdir(parents=True)
    (repo / "experiments" / "demo.py").write_text(_EXPERIMENT, encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def daemon(repository: Path, tmp_path: Path):
    settings = DaemonSettings(
        port=free_port("127.0.0.1", 18070),
        repositories=[RepositoryConfig(path=repository)],
        command=[sys.executable, "-m", "clabe.cli"],
        run_arguments=["--allow-dirty", "--skip-hardware-validation"],
        runs_directory=tmp_path / "runs",
    )
    host = Host(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            create_daemon_app(host, token="d4emon"), host="127.0.0.1", port=settings.port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{settings.port}", host
    current = host.current()
    if current is not None and current.process.poll() is None:
        current.process.kill()
    server.should_exit = True
    thread.join(5)


def _get(base: str, path: str, timeout: float = 30) -> requests.Response:
    return requests.get(f"{base}{path}", headers={"Authorization": "Bearer d4emon"}, timeout=timeout)


def test_a_run_is_launched_and_driven_through_the_daemon(daemon):
    base, host = daemon
    assert _get(base, "/api/daemon/info").json()["status"] == "idle"
    assert [r["name"] for r in _get(base, "/api/daemon/repositories").json()] == ["Aind.Experiment.Demo"]
    listing = _get(base, "/api/daemon/repositories/Aind.Experiment.Demo/experiments", timeout=120).json()
    assert [(e["name"], [s["name"] for s in e["steps"]]) for e in listing["experiments"]] == [
        ("demo", ["subject", "trials"])
    ]

    launched = requests.post(
        f"{base}/api/daemon/runs",
        json={"repository": "Aind.Experiment.Demo", "file": "experiments/demo.py"},
        headers={"Authorization": "Bearer d4emon"},
        timeout=30,
    )
    assert launched.ok, launched.text
    busy = requests.post(
        f"{base}/api/daemon/runs",
        json={"repository": "Aind.Experiment.Demo", "file": "experiments/demo.py"},
        headers={"Authorization": "Bearer d4emon"},
        timeout=30,
    )
    assert busy.status_code == 409

    deadline = time.monotonic() + 90
    while (run := _get(base, "/api/daemon/runs/current").json())["status"] == "starting":
        assert time.monotonic() < deadline, run["log_tail"]
        time.sleep(0.2)
    assert run["status"] == "running", run["log_tail"]
    assert _get(base, "/api/daemon/info").json()["status"] == "busy"

    # the run's own API and event stream, through the proxy, with the daemon's token
    client = RunClient(f"{base}/run", token="d4emon", name="hub")
    assert requests.get(f"{base}/run/api/state", timeout=10).status_code == 401
    answers = {"Subject": "mouse-7", "Trials": 30}
    for event in client.events():
        if isinstance(event, PromptOpened):
            assert client.answer(event.prompt_id, answers[event.spec["label"]]).ok
    assert host.current().process.wait(60) == 0
    run = _get(base, "/api/daemon/runs/current").json()
    assert run["status"] == "exited" and run["exit_code"] == 0
    assert _get(base, "/api/daemon/info").json()["status"] == "idle"
    assert requests.get(f"{base}/run/", timeout=10).status_code == 503  # nothing served any more


def test_the_daemon_needs_its_token(daemon):
    base, _ = daemon
    assert requests.get(f"{base}/api/daemon/info", timeout=10).status_code == 401
    assert requests.get(f"{base}/api/daemon/info?token=d4emon", timeout=10).ok


def test_an_unknown_repository_is_404(daemon):
    base, _ = daemon
    assert _get(base, "/api/daemon/repositories/nope/experiments").status_code == 404


@pytest.fixture(autouse=True)
def _not_from_uv_cache(monkeypatch):
    """The tests may run from uv's cache (``uv run --with ...``); installs here are not about that."""
    monkeypatch.setattr(_install, "_in_uv_cache", lambda: False)


class TestInstall:
    def test_the_task_starts_at_logon_and_restarts_on_failure(self):
        xml = _install.task_xml(["C:/tools/clabe.exe", "daemon", "run"], user="lab\\rig-3")
        assert "<LogonTrigger>" in xml and "<UserId>lab\\rig-3</UserId>" in xml
        assert "<RestartOnFailure>" in xml and "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
        assert "<Command>C:/tools/clabe.exe</Command><Arguments>daemon run</Arguments>" in xml

    def test_install_registers_and_starts_the_task(self, monkeypatch):
        calls = []

        def schtasks(*args):
            calls.append(args[0])
            return subprocess.CompletedProcess(args, 0, stdout="SUCCESS", stderr="")

        monkeypatch.setattr(_install, "_schtasks", schtasks)
        _install.install()
        _install.uninstall()
        assert calls == ["/Create", "/Run", "/End", "/Delete"]

    def test_a_failed_registration_is_an_error(self, monkeypatch):
        monkeypatch.setattr(
            _install, "_schtasks", lambda *a: subprocess.CompletedProcess(a, 1, stdout="", stderr="Access is denied.")
        )
        with pytest.raises(OSError, match="Access is denied"):
            _install.install()


def test_the_daemon_page_starts_an_experiment_and_opens_its_run(daemon):
    playwright_api = pytest.importorskip("playwright.sync_api")
    from clabe.server._app import STATIC_DIR

    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("the web UI is not built")
    import os

    base, host = daemon
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=os.environ.get("CLABE_TEST_BROWSER_CHANNEL") or None)
        except playwright_api.Error as exc:
            pytest.skip(f"no browser for Playwright: {exc}")
        page = browser.new_page()
        page.set_default_timeout(90_000)
        page.goto(f"{base}/?token=d4emon")
        page.get_by_role("button", name="Show experiments").click()
        page.get_by_role("button", name="Start").click()
        page.get_by_label("Subject").fill("mouse-9")  # the page opens the run once it is served
        page.get_by_role("button", name="Submit").click()
        page.get_by_label("Trials").fill("12")
        page.get_by_role("button", name="Submit").click()
        assert host.current().process.wait(60) == 0
        browser.close()


def test_a_token_starting_with_a_dash_reaches_the_run(daemon, monkeypatch):
    import clabe.daemon._host as host_module

    monkeypatch.setattr(host_module.secrets, "token_urlsafe", lambda n: "-starts-with-a-dash")
    base, host = daemon
    launched = requests.post(
        f"{base}/api/daemon/runs",
        json={"repository": "Aind.Experiment.Demo", "file": "experiments/demo.py"},
        headers={"Authorization": "Bearer d4emon"},
        timeout=30,
    )
    assert launched.ok
    deadline = time.monotonic() + 90
    while (run := _get(base, "/api/daemon/runs/current").json())["status"] == "starting":
        assert time.monotonic() < deadline, run["log_tail"]
        time.sleep(0.2)
    assert run["status"] == "running", run["log_tail"]
    host.current().process.kill()


class TestDeployment:
    """install --uvx, restart and status, with Task Scheduler stubbed out."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        calls, xml = [], {}

        def schtasks(*args):
            calls.append(args[0])
            if args[0] == "/Create":
                xml["text"] = Path(args[args.index("/XML") + 1]).read_text(encoding="utf-16")
            return subprocess.CompletedProcess(args, 0, stdout="SUCCESS", stderr="")

        monkeypatch.setattr(_install, "_schtasks", schtasks)
        monkeypatch.setattr(_install, "default_working_directory", lambda: tmp_path / "clabe")
        monkeypatch.setattr(_install.time, "sleep", lambda s: None)
        return calls, xml

    def test_install_with_uvx_pins_the_package_in_the_task(self, scheduler, monkeypatch, tmp_path):
        calls, xml = scheduler
        monkeypatch.setattr(_install.shutil, "which", lambda name: f"C:/uv/{name}.exe")
        monkeypatch.setattr(_install, "task_state", lambda: "Ready")
        message = _install.install(uvx="aind-clabe==0.18.0")
        assert "<Command>C:/uv/uvx.exe</Command>" in xml["text"]
        assert "<Arguments>--from aind-clabe==0.18.0 clabe daemon run</Arguments>" in xml["text"]
        assert f"<WorkingDirectory>{tmp_path / 'clabe'}</WorkingDirectory>" in xml["text"]
        assert calls == ["/Create", "/Run"] and (tmp_path / "clabe").is_dir() and "Registered" in message

    def test_installing_from_uvx_without_a_spec_is_refused(self, monkeypatch):
        monkeypatch.setattr(_install, "_in_uv_cache", lambda: True)  # after the autouse stub: wins
        with pytest.raises(OSError, match="--uvx"):
            _install.daemon_command()

    def test_reinstalling_says_the_running_daemon_needs_a_restart(self, scheduler, monkeypatch):
        monkeypatch.setattr(_install, "task_state", lambda: "Running")
        assert "clabe daemon restart" in _install.install()

    def test_restart_refuses_to_interrupt_a_session_unless_forced(self, scheduler, monkeypatch):
        calls, _ = scheduler
        monkeypatch.setattr(_install, "task_state", lambda: "Running")
        monkeypatch.setattr(_install, "probe", lambda port, token, timeout=2.0: {"status": "busy"})
        with pytest.raises(OSError, match="runs an experiment"):
            _install.restart(8070, None)
        assert calls == []

    def test_restart_ends_runs_and_waits_for_the_daemon(self, scheduler, monkeypatch):
        calls, _ = scheduler
        answers = iter(
            [{"status": "leased", "leased_by": "rig-1"}, None, None, {"status": "idle", "clabe_version": "9"}]
        )
        monkeypatch.setattr(_install, "task_state", lambda: "Running" if calls[-1:] == ["/Run"] else "Ready")
        monkeypatch.setattr(_install, "probe", lambda port, token, timeout=2.0: next(answers))
        assert _install.restart(8070, None, force=True) == "The clabe daemon answers on port 8070 (clabe 9)."
        assert calls == ["/End", "/Run"]

    def test_restart_explains_a_user_who_is_not_logged_on(self, scheduler, monkeypatch):
        monkeypatch.setattr(_install, "task_state", lambda: "Ready")
        monkeypatch.setattr(_install, "probe", lambda port, token, timeout=2.0: None)
        assert "logs on" in _install.restart(8070, None, timeout=0)


def test_probe_reads_the_daemon(daemon):
    base, _ = daemon
    port = int(base.rsplit(":", 1)[1])
    assert _install.probe(port, "d4emon")["status"] == "idle"
    assert _install.probe(port, "wrong") == {"status": "unauthorized"}
    assert _install.probe(free_port("127.0.0.1", 18170), None) is None
