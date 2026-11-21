import asyncio
import threading

from clabe.ui import ConfirmRequest
from clabe.workflow import Ack, GoBack, Rejected, Run, RunStatus, Workflow
from clabe.workflow._control import GoBackRejected
from clabe.workflow.events import Notify, PromptClosed, RunFinished, StepsInvalidated

from ._helpers import events_of, execute


def _session_rig_confirm(calls: list[str]) -> Workflow:
    wf = Workflow("demo")

    @wf.step
    def session():
        calls.append("session")

    @wf.step
    def rig():
        calls.append("rig")

    @wf.step
    def confirm(run, session):
        calls.append("confirm")
        if not run.ask(ConfirmRequest(label="ok?")):
            raise GoBack("session")

    @wf.step
    def register(rig):
        calls.append("register")

    return wf


def test_go_back_reruns_the_target_and_its_dependents_only():
    calls = []
    run, _ = execute(_session_rig_confirm(calls), [False, True])
    assert calls == ["session", "rig", "confirm", "session", "confirm", "register"]
    assert events_of(run, StepsInvalidated)[0].steps == ["session"]


def test_go_back_without_a_target_returns_to_the_previous_step():
    calls = []
    wf = Workflow("demo")
    wf.step(lambda: calls.append("a"), name="a")

    @wf.step
    def b(run):
        calls.append("b")
        if not run.ask(ConfirmRequest(label="?")):
            raise GoBack()

    execute(wf, [False, True])
    assert calls == ["a", "b", "a", "b"]


def test_client_go_back_cancels_the_open_prompt():
    calls = []
    wf = _session_rig_confirm(calls)

    run, client = execute(wf, [lambda run, event: run.go_back("rig"), True])

    assert client.results[0] == Ack()
    assert events_of(run, PromptClosed)[0].outcome == "cancelled"
    assert calls == ["session", "rig", "confirm", "rig", "confirm", "register"]


def test_cannot_go_back_past_a_completed_barrier():
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")
    wf.step(lambda: None, name="b", barrier=True)
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="c")

    def try_back(run, event):
        return run.go_back("a"), run.go_back("b"), run.answer(event.prompt_id, True)

    _, client = execute(wf, [try_back])

    back_a, back_b, answered = client.results[0]
    assert isinstance(back_a, Rejected) and "barrier" in back_a.reason
    assert isinstance(back_b, Rejected)
    assert answered == Ack()


def test_undo_runs_when_an_undoable_step_is_invalidated():
    undone = []
    wf = Workflow("demo")
    wf.step(lambda: "booked", name="book", undo=undone.append)

    @wf.step
    def confirm(run, book):
        if not run.ask(ConfirmRequest(label="?")):
            raise GoBack("book")

    execute(wf, [False, True])
    assert undone == ["booked"]


def test_go_back_is_rejected_while_a_sync_step_is_busy():
    started, release = threading.Event(), threading.Event()
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")

    @wf.step
    def busy():
        started.set()
        release.wait(5)

    run = Run(wf)
    run.start()
    assert started.wait(5)
    result = run.go_back("a")
    release.set()
    assert run.wait(5)
    assert isinstance(result, Rejected) and "busy" in result.reason


def test_a_cancellable_async_step_can_be_interrupted_by_go_back():
    calls = []
    started = threading.Event()
    wf = Workflow("demo")
    wf.step(lambda: calls.append("a"), name="a")

    @wf.step(cancellable=True)
    async def busy():
        calls.append("busy")
        if calls.count("busy") == 1:
            started.set()
            await asyncio.sleep(10)

    run = Run(wf)
    run.start()
    assert started.wait(5)
    assert run.go_back("a") == Ack()
    assert run.wait(5)
    assert calls == ["a", "busy", "a", "busy"]


def test_go_back_is_rejected_once_the_run_is_finishing():
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")
    run = Run(wf)
    results = []
    # RunFinished is published on the run thread after the last step, before the run counts as finished:
    # a go back accepted there would be acknowledged and then lost.
    run.events.subscribe(lambda event: isinstance(event, RunFinished) and results.append(run.go_back("a")))
    run.start()
    assert run.wait(5)
    assert results == [Rejected("The run is finishing.")]
    assert run.status is RunStatus.COMPLETED


def test_aborting_a_rejected_go_back_fails_the_run():
    wf = Workflow("demo")

    @wf.step
    def first():
        raise GoBack()

    run, _ = execute(wf, ["abort"])
    assert run.status is RunStatus.FAILED
    assert isinstance(run.error, GoBackRejected)
    assert events_of(run, Notify)


def test_aborting_a_go_back_to_a_step_that_has_not_run_fails_the_run():
    wf = Workflow("demo")

    @wf.step
    def first():
        raise GoBack("later")

    wf.step(lambda: None, name="later")

    run, _ = execute(wf, ["abort"])
    assert run.status is RunStatus.FAILED
    assert isinstance(run.error, GoBackRejected)
    assert "not run yet" in run.reason
