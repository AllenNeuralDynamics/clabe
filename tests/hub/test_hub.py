"""The hub end to end: a rig's daemon runs an experiment; the hub shows it, lists it in the inbox, and proxies it."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests
import uvicorn

from clabe.daemon import DaemonSettings, Host, RepositoryConfig, create_daemon_app
from clabe.hub import EnrollBody, Fleet, HubSettings, create_hub_app
from clabe.server import RunClient, free_port
from clabe.workflow.events import PromptOpened

_EXPERIMENT = """
from clabe import ui
from clabe.workflow import Workflow

demo = Workflow("demo", exit_prompt_timeout=0)


@demo.step(title="Subject")
def subject() -> str:
    return ui.text("Subject")
"""


def _serve(app, port: int):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    return server, thread


@pytest.fixture
def fleet(tmp_path: Path, end_launched_run):
    repo = tmp_path / "Aind.Experiment.Demo"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text(_EXPERIMENT, encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    hub_settings = HubSettings(port=free_port("127.0.0.1", 18280), poll_interval=0.3)
    fleet = Fleet(hub_settings)
    app = create_hub_app(fleet, token="hub-secret")
    hub, hub_thread = _serve(app, hub_settings.port)
    base = f"http://127.0.0.1:{hub_settings.port}"
    daemon_settings = DaemonSettings(
        port=free_port("127.0.0.1", 18270),
        repository_roots=[],
        repositories=[RepositoryConfig(path=repo)],
        command=[sys.executable, "-m", "clabe.cli"],
        run_arguments=["--allow-dirty", "--skip-hardware-validation"],
        runs_directory=tmp_path / "runs",
        file_transfer_dir=tmp_path / "transfer",
        hubs=[base],  # the daemon enrolls itself
        enroll_name="rig-3",
        enroll_host="127.0.0.1",
    )
    host = Host(daemon_settings)
    daemon, daemon_thread = _serve(create_daemon_app(host, token="rig-secret"), daemon_settings.port)
    nobody = {"id": "rig-9", "host": "127.0.0.1", "port": free_port("127.0.0.1", 18290)}  # nobody there
    enrolled = requests.post(f"{base}/api/hub/enroll", json=nobody, timeout=10)
    assert enrolled.ok, enrolled.text
    deadline = time.monotonic() + 10
    while "rig-3" not in fleet.rigs:
        assert time.monotonic() < deadline, "the daemon did not enroll"
        time.sleep(0.05)
    fleet.set_token("rig-3", "rig-secret")  # what one enters on the rig's card
    yield base
    end_launched_run(host)
    for server, thread in ((hub, hub_thread), (daemon, daemon_thread)):
        server.should_exit = True
        thread.join(5)


def _hub(base: str, path: str, method: str = "GET", **kwargs) -> requests.Response:
    return requests.request(
        method, f"{base}{path}", headers={"Authorization": "Bearer hub-secret"}, timeout=30, **kwargs
    )


def _launch_and_wait_for_prompt(base: str) -> dict:
    launched = _hub(
        base,
        "/rigs/rig-3/api/daemon/runs",
        "POST",
        json={"repository": "Aind.Experiment.Demo", "file": "main.py"},
    )
    assert launched.ok, launched.text
    deadline = time.monotonic() + 180  # a first launch on a CI runner can be slow
    while True:
        rigs = {r["id"]: r for r in _hub(base, "/api/hub/rigs").json()}
        if rigs["rig-3"]["waiting"] is not None:
            return rigs
        if time.monotonic() >= deadline:
            run = _hub(base, "/rigs/rig-3/api/daemon/runs/current").json()
            pytest.fail(f"No prompt after 180 s. Rig: {rigs['rig-3']} Run: {json.dumps(run, indent=1)}")
        time.sleep(0.3)


def test_the_hub_follows_a_rig_and_answers_through_it(fleet):
    base = fleet
    assert requests.get(f"{base}/api/hub/rigs", timeout=5).status_code == 401
    rigs = _launch_and_wait_for_prompt(base)
    rig = rigs["rig-3"]
    assert rig["url"].startswith("http://127.0.0.1:")  # built from the host it enrolled with
    assert (rig["status"], rig["experiment"], rig["current_step"]) == ("busy", "demo", "subject")
    assert rig["waiting"]["label"] == "Subject"
    assert (rigs["rig-9"]["reachable"], rigs["rig-9"]["status"]) == (False, "offline")
    assert [r["id"] for r in _hub(base, "/api/hub/inbox").json()] == ["rig-3"]

    # the run's API and event stream, through the hub and the rig's daemon, with the hub's token only
    client = RunClient(f"{base}/rigs/rig-3/run", token="hub-secret", name="hub-user")
    for event in client.events():
        if isinstance(event, PromptOpened):
            assert client.answer(event.prompt_id, "mouse-11").ok
    deadline = time.monotonic() + 60
    while {r["id"]: r for r in _hub(base, "/api/hub/rigs").json()}["rig-3"]["status"] != "idle":
        assert time.monotonic() < deadline
        time.sleep(0.3)
    assert _hub(base, "/api/hub/inbox").json() == []


def test_the_hub_page_leads_from_the_inbox_to_the_run(fleet):
    playwright_api = pytest.importorskip("playwright.sync_api")
    from clabe.server._app import STATIC_DIR

    if not (STATIC_DIR / "index.html").is_file():
        pytest.skip("the web UI is not built")
    base = fleet
    _launch_and_wait_for_prompt(base)
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=os.environ.get("CLABE_TEST_BROWSER_CHANNEL") or None)
        except playwright_api.Error as exc:
            pytest.skip(f"no browser for Playwright: {exc}")
        page = browser.new_page()
        page.set_default_timeout(30_000)
        page.goto(f"{base}/?token=hub-secret")
        page.get_by_role("link", name="Answer").click()
        page.get_by_label("Subject").fill("mouse-12")
        page.get_by_role("button", name="Submit").click()
        page.get_by_role("heading", name="Run completed").wait_for()
        # the logo leads back home: from the rig's run, the hub's page (not the rig's)
        page.get_by_role("link", name="clabe: home").click()
        page.wait_for_url(f"{base}/")  # the token stays in its cookie, out of the address
        page.get_by_role("tab", name="Rigs").wait_for()
        browser.close()


def test_polling_an_unreachable_rig_marks_it_offline():
    fleet = Fleet(HubSettings())
    fleet.enroll(EnrollBody(id="gone", host="127.0.0.1", port=free_port("127.0.0.1", 18295)))
    asyncio.run(fleet.poll_once())
    status = fleet.status["gone"]
    assert (status.reachable, status.status) == (False, "offline") and status.error
