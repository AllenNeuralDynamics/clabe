"""The run page's menus over HTTP: options from a function, back/forward, the closing screen, export and report."""

import io
import threading
import time
import zipfile

import pytest
import requests

from clabe import reports as clabe_reports
from clabe import ui
from clabe.server import RunClient, RunServer, free_port
from clabe.workflow import Run, RunStatus, Workflow
from clabe.workflow.events import RunClosed, RunClosing, RunFinished


@pytest.fixture
def serve():
    servers: list[RunServer] = []

    def start(run: Run) -> RunClient:
        server = RunServer(run, port=free_port("127.0.0.1", 18190))
        server.start()
        servers.append(server)
        return RunClient(f"http://127.0.0.1:{server.port}", name="pc-2")

    yield start
    for server in servers:
        server.stop()


def _wait_for_prompt(client: RunClient, step: str | None = None, timeout: float = 5.0):
    """The state once a prompt (of ``step``, when given) is open."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.state()
        if state.prompt is not None and (step is None or state.prompt.step == step):
            return state
        time.sleep(0.02)
    raise AssertionError("no prompt opened")


def test_back_and_forward_over_http(serve):
    wf = Workflow("demo")

    @wf.step
    def subject():
        return ui.text("Subject", field="subject")

    @wf.step
    def check(subject):
        return ui.confirm("ok?", field="check")

    run = Run(wf)
    client = serve(run)
    run.start()
    state = _wait_for_prompt(client, "subject")
    assert state.back_target is None and not state.prompt.can_forward
    assert not client.forward().ok
    assert client.answer(state.prompt.prompt_id, "mouse-7").ok

    state = _wait_for_prompt(client, "check")
    assert state.back_target == "subject"
    assert client.back().ok

    state = _wait_for_prompt(client, "subject")
    assert state.prompt.can_forward and state.prompt.previous_answer == "mouse-7"
    assert client.forward().ok

    state = _wait_for_prompt(client, "check")
    assert client.answer(state.prompt.prompt_id, True).ok
    assert run.wait(5) and run.output("subject") == "mouse-7"


def test_options_come_from_a_function_of_the_typed_text(serve):
    animals = ["mouse-7", "mouse-8", "rat-1"]
    calls: list[str] = []

    def search(typed: str) -> list[str]:
        calls.append(typed)
        if typed == "boom":
            raise RuntimeError("database down")
        return [a for a in animals if typed in a]

    wf = Workflow("demo")
    wf.step(lambda: ui.autocomplete("Subject", options=search, strict=True), name="a")
    run = Run(wf)
    client = serve(run)
    run.start()
    state = _wait_for_prompt(client)
    prompt = state.prompt
    assert prompt.spec["dynamic_options"] is True and prompt.spec["options"] == animals
    assert "enum" not in prompt.answer_schema

    assert client.options(prompt.prompt_id, "mouse") == ["mouse-7", "mouse-8"]
    with pytest.raises(requests.HTTPError) as error:
        client.options(prompt.prompt_id, "boom")
    assert error.value.response.status_code == 502 and "database down" in error.value.response.text
    with pytest.raises(requests.HTTPError) as error:
        client.options("not-a-prompt", "x")
    assert error.value.response.status_code == 404

    assert not client.answer(prompt.prompt_id, "dog").ok  # strict: the function does not return it
    assert client.answer(prompt.prompt_id, "rat-1").ok
    assert run.wait(5) and run.output("a") == "rat-1"


def test_options_of_a_group_item(serve):
    wf = Workflow("demo")
    wf.step(
        lambda: ui.group(
            subject=ui.AutoCompleteRequest(
                label="Subject", options=lambda t: [x for x in ("m-1", "m-2") if t in x], strict=True
            )
        ),
        name="a",
    )
    run = Run(wf)
    client = serve(run)
    run.start()
    prompt = _wait_for_prompt(client).prompt
    assert prompt.spec["items"]["subject"]["dynamic_options"] is True
    assert client.options(prompt.prompt_id, "1", item="subject") == ["m-1"]
    assert not client.answer(prompt.prompt_id, {"subject": "zzz"}).ok
    assert client.answer(prompt.prompt_id, {"subject": "m-1"}).ok
    assert run.wait(5) and run.output("a") == {"subject": "m-1"}


def test_closing_screen_is_closed_by_the_first_client(serve):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    run.keep_open()
    client = serve(run)
    assert not client.close().ok  # not finished yet
    events: list = []
    follower = threading.Thread(target=lambda: events.extend(client._stream(0)), daemon=True)
    follower.start()
    run.start()
    assert run.wait(5)
    run.begin_closing(None)
    state = client.state()
    assert state.closing is not None and state.closing.deadline is None and not state.closing.closed
    assert client.close().ok
    assert not client.close().ok  # the first close wins
    assert run.wait_closed(1) and client.state().closing.closed_by == "pc-2"
    follower.join(5)
    assert not follower.is_alive()  # the stream of a run kept open ends with RunClosed, not RunFinished
    types = [type(event) for event in events]
    assert types[-3:] == [RunFinished, RunClosing, RunClosed]


def test_export_and_report(serve, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    wf = Workflow("demo exp")
    wf.step(lambda: ui.text("Notes"), name="notes")
    run = Run(wf, record_dir=tmp_path / "records")
    client = serve(run)
    run.start()
    _wait_for_prompt(client)

    response = requests.get(f"{client.base_url}/api/export", timeout=10)
    assert response.headers["content-type"] == "application/zip"
    assert f'filename="demo_exp-{run.id}.zip"' in response.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(client.export())).namelist()
    assert "events.jsonl" in names and "rig.json" in names

    report = client.report("the camera froze")
    assert (report.comment, report.reported_from, report.run_id) == ("the camera froze", "pc-2", run.id)
    assert (report.experiment, report.step) == ("demo exp", "notes")
    saved = clabe_reports.reports_dir() / f"{report.report_id}.zip"  # temporary (see tests/conftest.py)
    assert "report.json" in zipfile.ZipFile(saved).namelist()

    run.abort()
    assert run.wait(5) and run.status is RunStatus.ABORTED


def test_report_without_records_includes_what_the_launcher_knows(serve, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    class FakeLauncher:
        def report_info(self):
            return {"session_id": "s-1", "subject": "mouse-7", "repository": "Aind.Behavior.X", "commit": "abc"}

    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf, launcher=FakeLauncher())
    client = serve(run)
    run.start()
    assert run.wait(5)
    report = client.report()
    assert (report.session_id, report.subject, report.repository, report.commit) == (
        "s-1",
        "mouse-7",
        "Aind.Behavior.X",
        "abc",
    )
    assert zipfile.ZipFile(io.BytesIO(client.export())).namelist()  # no records: still a bundle


def test_a_browser_following_the_events_watches_the_run(serve):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    run.keep_open()
    client = serve(run)
    assert run.viewers == 0
    follower = threading.Thread(target=lambda: list(client._stream(0)), daemon=True)
    follower.start()
    deadline = time.monotonic() + 5
    while run.viewers == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert run.viewers == 1
    run.start()
    assert run.wait(5)
    run.begin_closing(None)
    assert run.viewers == 1  # still watching the closing screen
    assert client.close().ok
    follower.join(5)
    deadline = time.monotonic() + 5
    while run.viewers and time.monotonic() < deadline:
        time.sleep(0.02)
    assert run.viewers == 0  # the stream ended: nobody watches
