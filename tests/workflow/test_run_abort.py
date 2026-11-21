import asyncio
import threading
import time

import pytest

from clabe.ui import ConfirmRequest, PromptCancelledError
from clabe.workflow import Abort, Ack, GoBack, Rejected, Run, RunStatus, StepStatus, Workflow
from clabe.workflow._run import _Command, _Stop
from clabe.workflow.events import PromptClosed, RunFinished, StepFailed, StepStarted

from ._helpers import ScriptedClient, events_of, execute


def test_abort_while_a_prompt_is_open():
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")
    wf.step(lambda: None, name="b")

    run, client = execute(wf, [lambda run, event: run.abort("stop")])

    assert client.results[0] == Ack()
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")
    assert [e.step for e in events_of(run, StepStarted)] == ["a"]
    assert events_of(run, RunFinished)[0].status == "aborted"


def test_a_step_can_abort_the_run():
    wf = Workflow("demo")

    @wf.step
    def a():
        raise Abort("no subject")

    run, _ = execute(wf)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "no subject")


def _abort_when_started(wf: Workflow, started: threading.Event) -> Run:
    run = Run(wf)
    run.start()
    assert started.wait(5)
    run.abort()
    assert run.wait(5), "abort did not stop the run"
    return run


def test_abort_interrupts_a_busy_async_step():
    started = threading.Event()
    wf = Workflow("demo")

    @wf.step
    async def busy():
        started.set()
        await asyncio.sleep(10)

    assert _abort_when_started(wf, started).status is RunStatus.ABORTED


def test_abort_interrupts_a_busy_sync_step():
    started = threading.Event()
    wf = Workflow("demo")

    @wf.step
    def busy():
        started.set()
        while True:
            time.sleep(0.01)

    run = _abort_when_started(wf, started)
    assert (run.status, run.reason) == (RunStatus.ABORTED, "Aborted by user.")
    assert not run._in_sync_call


def test_go_back_cannot_override_a_pending_abort():
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="b")
    run = Run(wf)
    results = []
    # Published while abort() still holds the lock: the abort is pending and not yet applied.
    run.events.subscribe(
        lambda event: (
            isinstance(event, PromptClosed) and event.outcome == "cancelled" and results.append(run.go_back("a"))
        )
    )
    client = ScriptedClient(run, [lambda run, event: run.abort("stop")])
    run.start()
    assert run.wait(5)
    for thread in client.threads:
        thread.join(5)

    assert client.results == [Ack()]
    assert results == [Rejected("The run is aborting.")]
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")


def test_a_second_abort_keeps_the_first_reason():
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")

    run, client = execute(wf, [lambda run, event: (run.abort("first"), run.abort("second"))])

    assert client.results[0] == (Ack(), Ack())
    assert (run.status, run.reason) == (RunStatus.ABORTED, "first")


def test_abort_has_no_effect_once_the_run_is_finishing_or_finished():
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")
    run = Run(wf)
    results = []
    run.events.subscribe(lambda event: isinstance(event, RunFinished) and results.append(run.abort("late")))
    run.start()
    assert run.wait(5)

    assert results == [Ack()]
    assert run.abort("after") == Ack()
    assert (run.status, run.reason) == (RunStatus.COMPLETED, None)
    assert events_of(run, RunFinished)[0].status == "completed"


def _demo_run() -> tuple[Run, Workflow]:
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")

    @wf.step
    def b():
        raise Abort("no subject")

    return Run(wf), wf


def test_a_pending_abort_refuses_go_back_until_the_run_has_finished():
    run, _ = _demo_run()
    run._pending = _Command("abort", reason="stop")

    with pytest.raises(_Stop):
        run._apply_pending()

    assert run.go_back("a") == Rejected("The run is finishing.")


def test_a_step_abort_refuses_go_back_until_the_run_has_finished():
    run, wf = _demo_run()

    with pytest.raises(_Stop):
        asyncio.run(run._run_step(wf.get("b")))

    assert run.go_back("a") == Rejected("The run is finishing.")


def test_aborting_after_a_failure_refuses_go_back_until_the_run_has_finished():
    run, wf = _demo_run()
    run.status = RunStatus.RUNNING  # prompts only open on a running run
    client = ScriptedClient(run, ["abort"])

    with pytest.raises(_Stop):
        run._fail(wf.get("b"), RuntimeError("boom"))
    for thread in client.threads:
        thread.join(5)

    assert run.go_back("a") == Rejected("The run is finishing.")


def test_a_rejected_go_back_keeps_where_it_was_raised():
    wf = Workflow("demo")

    @wf.step
    def first():
        raise GoBack()

    run, _ = execute(wf, ["abort"])

    assert isinstance(run.error.__cause__, GoBack)
    assert run._steps["first"] is StepStatus.FAILED
    failed = events_of(run, StepFailed)[0].traceback
    assert "raise GoBack()" in failed and "direct cause" in failed


def _never_ending(kind: str) -> Workflow:
    wf = Workflow("demo")
    if kind == "sync":

        @wf.step
        def busy():
            while True:
                time.sleep(0.01)

    else:

        @wf.step
        async def busy():
            await asyncio.sleep(10)

    return wf


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("from_thread", [False, True])
def test_an_abort_arriving_as_the_step_starts_stops_it(kind, from_thread):
    run = Run(_never_ending(kind))

    def on_event(event):
        if isinstance(event, StepStarted):
            # Before the step is busy: neither the sync flag nor the async task is set yet.
            if from_thread:
                threading.Thread(target=run.abort, args=("stop",), daemon=True).start()
            else:
                run.abort("stop")

    run.events.subscribe(on_event)
    run.start()
    assert run.wait(5), "abort did not stop the run"
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")


def test_an_abort_arriving_before_the_async_task_is_tracked_cancels_it(monkeypatch):
    run = Run(_never_ending("async"))
    ensure_future = asyncio.ensure_future

    def abort_then_ensure_future(awaitable):
        run.abort("stop")  # the sync call has returned and the task is not tracked yet
        return ensure_future(awaitable)

    monkeypatch.setattr(asyncio, "ensure_future", abort_then_ensure_future)
    run.start()
    assert run.wait(5), "abort did not stop the run"
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")


def test_a_client_abort_keeps_its_reason_when_the_step_then_aborts():
    wf = Workflow("demo")

    @wf.step
    def a(run):
        try:
            run.ask(ConfirmRequest(label="?"))
        except PromptCancelledError:
            raise Abort("step reason") from None

    run, _ = execute(wf, [lambda run, event: run.abort("client reason")])
    assert (run.status, run.reason) == (RunStatus.ABORTED, "client reason")


def test_abort_while_the_failure_pick_is_open():
    wf = Workflow("demo")

    @wf.step
    def broken():
        raise RuntimeError("boom")

    run, client = execute(wf, [lambda run, event: run.abort("stop")])
    assert client.prompts[0].kind == "pick"
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")


def test_abort_while_an_optional_confirm_is_open():
    ran = []
    wf = Workflow("demo")
    wf.step(lambda: ran.append("qc"), name="qc", optional="QC?")

    run, client = execute(wf, [lambda run, event: run.abort("stop")])
    assert client.prompts[0].kind == "confirm"
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")
    assert ran == []


def test_abort_overrides_a_pending_go_back():
    calls = []
    wf = Workflow("demo")
    wf.step(lambda: calls.append("a"), name="a")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="b")
    run = Run(wf)
    results = []
    # Published while go_back() still holds the lock: the go back is pending and not yet applied.
    run.events.subscribe(
        lambda event: (
            isinstance(event, PromptClosed) and event.outcome == "cancelled" and results.append(run.abort("stop"))
        )
    )
    client = ScriptedClient(run, [lambda run, event: run.go_back("a")])
    run.start()
    assert run.wait(5)
    for thread in client.threads:
        thread.join(5)

    assert client.results == [Ack()] and results == [Ack()]
    assert (run.status, run.reason) == (RunStatus.ABORTED, "stop")
    assert calls == ["a"]
