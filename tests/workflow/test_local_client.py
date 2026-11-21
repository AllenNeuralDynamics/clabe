import threading
import time

import pytest

from clabe.ui import ConfirmRequest, MessageLevel, PickRequest, PromptCancelledError, TextRequest
from clabe.workflow import LocalClient, Run, RunStatus, Workflow
from clabe.workflow.events import PromptClosed, PromptOpened, RunFinished
from tests.conftest import MockFrontend

from ._helpers import events_of


def _drive(wf, frontend) -> Run:
    run = Run(wf)
    LocalClient(run, frontend).drive()
    return run


def _cancel_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "clabe-cancel-prompt"]


def test_answers_prompts_and_renders_messages(mock_frontend):
    mock_frontend._ask_confirm_mock.return_value = False
    wf = Workflow("demo")

    @wf.step
    def a(run):
        run.notify("hi")
        return run.ask(ConfirmRequest(label="?"))

    run = _drive(wf, mock_frontend)

    assert run.status is RunStatus.COMPLETED and run.output("a") is False
    mock_frontend._render_mock.assert_any_call("hi", MessageLevel.INFO)
    assert events_of(run, PromptClosed)[0].answered_by == "local"


def test_rejected_answers_are_reported_and_asked_again(mock_frontend):
    mock_frontend._ask_pick_mock.side_effect = ["zzz", "b"]
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(PickRequest(label="p", options=["a", "b"])), name="a")

    run = _drive(wf, mock_frontend)

    assert run.output("a") == "b"
    rendered = [c.args[0] for c in mock_frontend._render_mock.call_args_list]
    assert any("not one of the options" in message for message in rendered)


def test_a_stray_local_cancel_asks_again_while_the_prompt_is_open(mock_frontend):
    mock_frontend._ask_text_mock.side_effect = [PromptCancelledError(), "again"]
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)
    driver = threading.Thread(target=LocalClient(run, mock_frontend).drive, daemon=True)  # a hang fails, not blocks

    driver.start()
    driver.join(5)

    assert not driver.is_alive()
    assert run.output("a") == "again"


class _BlockingFrontend(MockFrontend):
    """Blocks in the text prompt until cancel_prompt is called."""

    def __init__(self):
        super().__init__()
        self.cancelled = threading.Event()

    def _ask_text(self, request):
        assert self.cancelled.wait(5)
        raise PromptCancelledError()

    def cancel_prompt(self):
        self.cancelled.set()


def test_a_prompt_answered_elsewhere_cancels_the_local_prompt():
    frontend = _BlockingFrontend()
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)

    def answer_remotely(event):
        if isinstance(event, PromptOpened):
            threading.Timer(0.2, run.answer, args=(event.prompt_id, "remote"), kwargs={"client": "pc-2"}).start()

    run.events.subscribe(answer_remotely)
    LocalClient(run, frontend).drive()

    assert run.output("a") == "remote"
    assert frontend.cancelled.is_set()
    deadline = time.monotonic() + 2
    while _cancel_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _cancel_threads()


class _UncancellableFrontend(MockFrontend):
    """Cannot cancel its text prompt: it returns only once the prompt was closed elsewhere."""

    def __init__(self):
        super().__init__()
        self.closed = threading.Event()

    def _ask_text(self, request):
        assert self.closed.wait(5)
        return "local"


def test_a_local_answer_to_a_prompt_answered_elsewhere_is_reported():
    frontend = _UncancellableFrontend()
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)

    def on_event(event):
        if isinstance(event, PromptOpened):
            threading.Timer(0.1, run.answer, args=(event.prompt_id, "remote"), kwargs={"client": "pc-2"}).start()
        elif isinstance(event, PromptClosed):
            frontend.closed.set()

    run.events.subscribe(on_event)
    LocalClient(run, frontend).drive()

    assert run.output("a") == "remote"
    frontend._render_mock.assert_any_call("This prompt was answered elsewhere.", MessageLevel.INFO)


def test_drive_returns_when_the_run_finished_without_publishing_it(mock_frontend):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    append = run.events.append

    def failing_append(event):
        if isinstance(event, RunFinished):
            raise OSError("disk full")
        return append(event)

    run.events.append = failing_append
    driver = threading.Thread(target=LocalClient(run, mock_frontend).drive, daemon=True)  # a hang fails, not blocks

    driver.start()
    driver.join(5)

    assert not driver.is_alive()
    assert run.status is RunStatus.COMPLETED


def test_a_failing_frontend_aborts_the_run_and_propagates(mock_frontend):
    mock_frontend._ask_text_mock.side_effect = EOFError
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="name")), name="a")
    run = Run(wf)

    with pytest.raises(EOFError):
        LocalClient(run, mock_frontend).drive()

    assert run.wait(5)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "The local frontend failed.")


def test_driving_a_run_already_started_elsewhere_leaves_it_alone(mock_frontend):
    release = threading.Event()
    wf = Workflow("demo")
    wf.step(lambda: release.wait(5), name="a")
    run = Run(wf)
    run.start()

    with pytest.raises(RuntimeError):
        LocalClient(run, mock_frontend).drive()

    release.set()
    assert run.wait(5)
    assert run.status is RunStatus.COMPLETED


def test_ctrl_c_aborts_the_run(mock_frontend):
    mock_frontend._ask_confirm_mock.side_effect = KeyboardInterrupt
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")

    run = _drive(wf, mock_frontend)

    assert (run.status, run.reason) == (RunStatus.ABORTED, "Interrupted by user.")


def test_ctrl_c_aborts_before_telling_the_user(mock_frontend):
    calls = []
    mock_frontend._ask_confirm_mock.side_effect = KeyboardInterrupt
    mock_frontend._render_mock.side_effect = lambda message, level: calls.append(("notify", message))
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")
    run = Run(wf)
    abort = run.abort
    run.abort = lambda reason: (calls.append(("abort", reason)), abort(reason))[1]

    LocalClient(run, mock_frontend).drive()

    assert calls[0] == ("abort", "Interrupted by user.")
    assert calls[1][0] == "notify"


def test_a_second_ctrl_c_propagates_and_the_run_still_ends(mock_frontend):
    mock_frontend._ask_confirm_mock.side_effect = KeyboardInterrupt
    mock_frontend._render_mock.side_effect = KeyboardInterrupt  # the user presses Ctrl+C again while warned
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")
    run = Run(wf)

    with pytest.raises(KeyboardInterrupt):
        LocalClient(run, mock_frontend).drive()

    assert run.wait(5)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "Interrupted by user.")
