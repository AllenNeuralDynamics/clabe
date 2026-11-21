"""End to end: a real run behind a real server, driven over HTTP by the Python client."""

import threading

import pytest
import requests

from clabe import ui
from clabe.server import RunClient, RunServer
from clabe.workflow import GoBack, Run, RunStatus, Workflow
from clabe.workflow.events import PromptClosed, PromptOpened, RunFinished


@pytest.fixture
def serve():
    servers: list[RunServer] = []

    def start(run: Run, **kwargs) -> RunClient:
        server = RunServer(run, port=18090, **kwargs)
        server.start()
        servers.append(server)
        return RunClient(f"http://127.0.0.1:{server.port}", token=kwargs.get("token"), name="pc-2")

    yield start
    for server in servers:
        server.stop()


def _answering(client: RunClient, answers: dict[str, object]) -> threading.Thread:
    """Follows the events and answers each prompt by its label (or first word of a pick label)."""

    def follow() -> None:
        for event in client.events():
            if isinstance(event, PromptOpened):
                label = event.spec.get("label", "")
                client.answer(event.prompt_id, answers[label])

    thread = threading.Thread(target=follow, daemon=True)
    thread.start()
    return thread


def test_a_remote_client_drives_a_run(serve):
    wf = Workflow("demo")

    @wf.step
    def name():
        return ui.text("Subject")

    @wf.step
    def trials(name):
        return ui.number("Trials", minimum=1)

    run = Run(wf)
    client = serve(run)
    thread = _answering(client, {"Subject": "mouse", "Trials": 12})
    run.start()
    assert run.wait(10)
    thread.join(5)
    assert run.status is RunStatus.COMPLETED
    assert (run.output("name"), run.output("trials")) == ("mouse", 12.0)
    closed = [e for e in run.events.since(0) if isinstance(e, PromptClosed)]
    assert {e.answered_by for e in closed} == {"pc-2"}


def test_state_info_and_rejections(serve):
    wf = Workflow("demo")
    opened = threading.Event()

    @wf.step
    def a():
        opened.set()
        return ui.number("Gain", minimum=0, maximum=1)

    run = Run(wf)
    client = serve(run)
    run.start()
    assert opened.wait(5)
    state = None
    for _ in range(100):
        state = client.state()
        if state.prompt is not None:
            break
    assert state.prompt.kind == "number" and state.current_step == "a"
    assert client.info().workflow == "demo"
    rejected = client.answer(state.prompt.prompt_id, 5)
    assert not rejected.ok and "≤ 1" in rejected.reason
    assert client.answer(state.prompt.prompt_id, 0.5).ok
    assert run.wait(5) and run.output("a") == 0.5


def test_events_resume_after_a_sequence_number(serve):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    wf.step(lambda: 2, name="b")
    run = Run(wf)
    client = serve(run)
    run.start()
    assert run.wait(5)
    everything = list(client.events())
    assert isinstance(everything[-1], RunFinished)
    resumed = list(client.events(after=everything[2].seq))
    assert [e.seq for e in resumed] == [e.seq for e in everything[3:]]


def test_back_retry_skip_and_abort(serve):
    calls: list[str] = []
    failed_once = threading.Event()
    wf = Workflow("demo")

    @wf.step
    def session():
        calls.append("session")

    @wf.step
    def flaky(session):
        calls.append("flaky")
        if not failed_once.is_set():
            failed_once.set()
            raise RuntimeError("boom")

    @wf.step(optional="Generate QC?")
    def qc():
        calls.append("qc")

    @wf.step
    def confirm(run):
        if not ui.confirm("Done?"):
            raise GoBack("session")

    run = Run(wf)
    client = serve(run)
    decisions = iter(["retry", "skip", "back", "confirm"])

    def follow() -> None:
        for event in client.events():
            if not isinstance(event, PromptOpened):
                continue
            decision = next(decisions, "confirm")
            if decision == "retry":
                assert client.retry("flaky").ok
            elif decision == "skip":
                assert client.skip("qc").ok
            elif decision == "back":
                assert not client.back("nope").ok
                assert client.back("session").ok
            else:
                client.answer(event.prompt_id, True)

    thread = threading.Thread(target=follow, daemon=True)
    thread.start()
    run.start()
    assert run.wait(10)
    thread.join(5)
    assert run.status is RunStatus.COMPLETED
    # flaky depends on session, so going back to session runs it again; the skipped qc does not
    assert calls == ["session", "flaky", "flaky", "session", "flaky"]


def test_abort_over_http(serve):
    wf = Workflow("demo")
    wf.step(lambda: ui.confirm("Wait?"), name="a")
    run = Run(wf)
    client = serve(run)

    def follow() -> None:
        for event in client.events():
            if isinstance(event, PromptOpened):
                assert client.abort("from the hub").ok

    thread = threading.Thread(target=follow, daemon=True)
    thread.start()
    run.start()
    assert run.wait(10)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "from the hub")


def test_the_token_is_required_when_set(serve):
    wf = Workflow("demo")
    run = Run(wf)
    client = serve(run, token="s3cret")
    assert client.info().workflow == "demo"
    anonymous = RunClient(client.base_url)
    with pytest.raises(requests.HTTPError) as error:
        anonymous.info()
    assert error.value.response.status_code == 401
    assert requests.get(f"{client.base_url}/api/info?token=s3cret", timeout=5).ok


def test_the_remote_path_picker_lists_directories(serve, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "rig.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("x")
    client = serve(Run(Workflow("demo")))
    listing = client.list_dir(str(tmp_path), extensions=[".json"])
    assert [(e.name, e.is_dir) for e in listing.entries] == [("sub", True), ("rig.json", False)]
    assert listing.parent == str(tmp_path.parent)
    assert [e.name for e in client.list_dir(str(tmp_path), kind="dir").entries] == ["sub"]


def test_a_busy_port_moves_to_the_next_free_one(serve):
    first = serve(Run(Workflow("one")))
    second = serve(Run(Workflow("two")))
    assert first.base_url != second.base_url
    assert (first.info().workflow, second.info().workflow) == ("one", "two")


def test_the_openapi_document_describes_the_api(serve):
    client = serve(Run(Workflow("demo")))
    spec = requests.get(f"{client.base_url}/openapi.json", timeout=5).json()
    assert {
        "/api/info",
        "/api/state",
        "/api/events",
        "/api/prompts/{prompt_id}/answer",
        "/api/steps/{name}/back",
        "/api/steps/{name}/retry",
        "/api/steps/{name}/skip",
        "/api/abort",
        "/api/fs/list",
    } <= set(spec["paths"])
    stream = spec["paths"]["/api/events"]["get"]["responses"]["200"]["content"]["text/event-stream"]
    assert "itemSchema" in stream
    assert {"RunState", "PromptOpened", "CommandResult"} <= set(spec["components"]["schemas"])


class _BlockingClient:
    """A local client (standing in for the TUI) whose prompt stays open until it is cancelled."""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.asked: list = []
        self.notes: list = []

    def ask(self, request):
        from clabe.ui import PromptCancelledError

        self.asked.append(request)
        self.cancelled.wait(10)
        self.cancelled.clear()
        raise PromptCancelledError()

    def cancel(self):
        self.cancelled.set()

    def notify(self, message, level=None):
        self.notes.append(message)

    def header(self, text):
        pass

    def close(self):
        pass


def test_the_local_frontend_and_the_web_share_one_run(serve):
    from clabe.workflow import LocalClient

    wf = Workflow("demo")
    wf.step(lambda: ui.text("Subject"), name="a")
    run = Run(wf)
    client = serve(run)
    local = _BlockingClient()
    thread = _answering(client, {"Subject": "from the web"})
    driver = threading.Thread(target=LocalClient(run, local).drive, daemon=True)
    driver.start()
    driver.join(10)
    thread.join(5)
    assert run.status is RunStatus.COMPLETED and run.output("a") == "from the web"
    assert [r.label for r in local.asked][:1] == ["Subject"]  # the TUI showed it; the web answer closed it


def test_a_media_prompt_serves_its_own_file_and_nothing_else(serve, tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG fake")
    opened = threading.Event()
    wf = Workflow("demo")

    @wf.step
    def a():
        opened.set()
        ui.image(image, caption="Camera 1")

    @wf.step
    def b(a):
        return ui.text("Name")

    run = Run(wf)
    client = serve(run, token="k")
    run.start()
    assert opened.wait(5)
    for _ in range(100):
        state = client.state()
        if state.prompt is not None:
            break
    media = state.prompt
    assert media.kind == "media" and media.spec["location"] == "file"
    response = requests.get(f"{client.base_url}/api/prompts/{media.prompt_id}/media?token=k", timeout=5)
    assert response.ok and response.content == image.read_bytes()
    assert response.headers["content-type"] == "image/png"
    assert client.answer(media.prompt_id, None).ok
    for _ in range(100):
        state = client.state()
        if state.prompt is not None and state.prompt.prompt_id != media.prompt_id:
            break
    other = requests.get(f"{client.base_url}/api/prompts/{state.prompt.prompt_id}/media?token=k", timeout=5)
    assert other.status_code == 404  # a text prompt shows no file
    assert requests.get(f"{client.base_url}/api/prompts/{media.prompt_id}/media", timeout=5).status_code == 401
    run.abort("done")
