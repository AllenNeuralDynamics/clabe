import threading
import time

import pytest

from clabe.ui import ConfirmRequest, MessageLevel, PickRequest, PromptCancelledError, TextRequest
from clabe.workflow import LocalClient, Run, RunStatus, Workflow
from clabe.workflow.events import PromptClosed, PromptOpened, RunFinished
from tests.conftest import MockClient

from ._helpers import events_of


def _drive(wf, client) -> Run:
    run = Run(wf)
    LocalClient(run, client).drive()
    return run


def _cancel_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "clabe-cancel-prompt"]


def test_answers_prompts_and_renders_messages(mock_client):
    mock_client.answers["confirm"] = False
    wf = Workflow("demo")

    @wf.step
    def a(run):
        run.notify("hi")
        run.header("Title")
        return run.ask(ConfirmRequest(label="?"))

    run = _drive(wf, mock_client)

    assert run.status is RunStatus.COMPLETED and run.output("a") is False
    assert ("hi", MessageLevel.INFO) in mock_client.notifications
    assert mock_client.headers == ["Title"]
    assert isinstance(mock_client.asked[0], ConfirmRequest)
    assert events_of(run, PromptClosed)[0].answered_by == "local"


def test_rejected_answers_are_reported_and_asked_again(mock_client):
    mock_client.answers["pick"] = ["zzz", "b"]
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(PickRequest(label="p", options=["a", "b"])), name="a")

    run = _drive(wf, mock_client)

    assert run.output("a") == "b"
    assert any(
        "not one of the options" in message and level is MessageLevel.ERROR
        for message, level in mock_client.notifications
    )


def test_a_stray_local_cancel_asks_again_while_the_prompt_is_open(mock_client):
    mock_client.answers["text"] = [PromptCancelledError(), "again"]
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)
    driver = threading.Thread(target=LocalClient(run, mock_client).drive, daemon=True)  # a hang fails, not blocks

    driver.start()
    driver.join(5)

    assert not driver.is_alive()
    assert run.output("a") == "again"


def test_a_prompt_answered_elsewhere_cancels_the_local_prompt():
    client = MockClient()

    def blocks_until_cancelled(request):
        assert client.cancelled.wait(5)
        raise PromptCancelledError()

    client.answers["text"] = blocks_until_cancelled
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)

    def answer_remotely(event):
        if isinstance(event, PromptOpened):
            threading.Timer(0.2, run.answer, args=(event.prompt_id, "remote"), kwargs={"client": "pc-2"}).start()

    run.events.subscribe(answer_remotely)
    LocalClient(run, client).drive()

    assert run.output("a") == "remote"
    assert client.cancelled.is_set()
    deadline = time.monotonic() + 2
    while _cancel_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _cancel_threads()


def test_a_local_answer_to_a_prompt_answered_elsewhere_is_reported():
    client = MockClient()
    closed = threading.Event()

    def cannot_be_cancelled(request):  # returns only once the prompt was closed elsewhere
        assert closed.wait(5)
        return "local"

    client.answers["text"] = cannot_be_cancelled
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)

    def on_event(event):
        if isinstance(event, PromptOpened):
            threading.Timer(0.1, run.answer, args=(event.prompt_id, "remote"), kwargs={"client": "pc-2"}).start()
        elif isinstance(event, PromptClosed):
            closed.set()

    run.events.subscribe(on_event)
    LocalClient(run, client).drive()

    assert run.output("a") == "remote"
    assert ("This prompt was answered elsewhere.", MessageLevel.INFO) in client.notifications


def test_drive_returns_when_the_run_finished_without_publishing_it(mock_client):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    append = run.events.append

    def failing_append(event):
        if isinstance(event, RunFinished):
            raise OSError("disk full")
        return append(event)

    run.events.append = failing_append
    driver = threading.Thread(target=LocalClient(run, mock_client).drive, daemon=True)  # a hang fails, not blocks

    driver.start()
    driver.join(5)

    assert not driver.is_alive()
    assert run.status is RunStatus.COMPLETED


def test_a_failing_client_aborts_the_run_and_propagates(mock_client):
    mock_client.answers["text"] = EOFError
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)

    with pytest.raises(EOFError):
        LocalClient(run, mock_client).drive()

    assert run.wait(5)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "The local client failed.")


def test_driving_a_run_already_started_elsewhere_leaves_it_alone(mock_client):
    release = threading.Event()
    wf = Workflow("demo")
    wf.step(lambda: release.wait(5), name="a")
    run = Run(wf)
    run.start()

    with pytest.raises(RuntimeError):
        LocalClient(run, mock_client).drive()

    release.set()
    assert run.wait(5)
    assert run.status is RunStatus.COMPLETED


def test_ctrl_c_aborts_the_run(mock_client):
    mock_client.answers["confirm"] = KeyboardInterrupt
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")

    run = _drive(wf, mock_client)

    assert (run.status, run.reason) == (RunStatus.ABORTED, "Interrupted by user.")


def test_ctrl_c_aborts_before_telling_the_user(mock_client):
    calls = []
    mock_client.answers["confirm"] = KeyboardInterrupt
    mock_client.on_notify = lambda message, level: calls.append(("notify", message))
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")
    run = Run(wf)
    abort = run.abort
    run.abort = lambda reason: (calls.append(("abort", reason)), abort(reason))[1]

    LocalClient(run, mock_client).drive()

    assert calls[0] == ("abort", "Interrupted by user.")
    assert calls[1][0] == "notify"


def test_a_second_ctrl_c_propagates_and_the_run_still_ends(mock_client):
    mock_client.answers["confirm"] = KeyboardInterrupt

    def interrupted_again(message, level):  # the user presses Ctrl+C again while warned
        raise KeyboardInterrupt

    mock_client.on_notify = interrupted_again
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")
    run = Run(wf)

    with pytest.raises(KeyboardInterrupt):
        LocalClient(run, mock_client).drive()

    assert run.wait(5)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "Interrupted by user.")
