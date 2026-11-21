import asyncio
import contextvars
import threading
import time

import pytest

from clabe.workflow import Run, RunStatus, Workflow
from clabe.workflow.events import EventLog, RunFinished, RunStarted, StepFinished, StepStarted

from ._helpers import events_of, execute

_VAR: contextvars.ContextVar[str | None] = contextvars.ContextVar("_VAR", default=None)


def test_runs_steps_in_order_and_wires_outputs():
    wf = Workflow("demo")

    @wf.step
    def a():
        return 1

    @wf.step
    async def b(a):
        await asyncio.sleep(0)
        return a + 1

    @wf.step
    def c(a, b, run):
        return (a, b, run.id)

    run, _ = execute(wf)

    assert run.status is RunStatus.COMPLETED
    assert run.output("c") == (1, 2, run.id)
    assert [e.step for e in events_of(run, StepStarted)] == ["a", "b", "c"]
    assert [e.summary for e in events_of(run, StepFinished)][:2] == ["1", "2"]
    assert isinstance(run.events.since(0)[0], RunStarted)
    assert events_of(run, RunFinished)[0].status == "completed"


def test_launcher_builtin_is_injected():
    wf = Workflow("demo")
    sentinel = object()
    wf.step(lambda launcher: launcher, name="a")
    run, _ = execute(wf, launcher=sentinel)
    assert run.output("a") is sentinel


def test_sync_step_returning_an_awaitable_is_awaited():
    async def work():
        return 5

    wf = Workflow("demo")
    wf.step(lambda: work(), name="a")
    run, _ = execute(wf)
    assert run.output("a") == 5


def test_a_sync_step_can_run_its_own_event_loop():
    async def work():
        await asyncio.sleep(0)
        return 5

    wf = Workflow("demo")
    wf.step(lambda: asyncio.run(work()), name="a")

    @wf.step
    def b(a):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(work()) + a
        finally:
            loop.close()

    run, _ = execute(wf)
    assert run.status is RunStatus.COMPLETED
    assert run.output("b") == 10


def test_tasks_started_by_a_step_keep_running_into_later_steps():
    wf = Workflow("demo")

    @wf.step
    async def a():
        async def answer():
            await asyncio.sleep(0.05)
            return 7

        return {"answer": asyncio.create_task(answer()), "forever": asyncio.create_task(asyncio.sleep(60))}

    @wf.step
    def middle(a):
        time.sleep(0.01)  # a sync step in between does not stop the loop the tasks run on

    @wf.step
    async def b(a, middle):
        alive = [(t.done(), t.cancelled()) for t in a.values()]
        a["forever"].cancel()
        try:
            await a["forever"]
        except asyncio.CancelledError:
            pass
        return alive, await a["answer"], a["forever"].cancelled()

    run, _ = execute(wf)

    assert run.status is RunStatus.COMPLETED
    assert run.output("b") == ([(False, False), (False, False)], 7, True)


def test_tasks_left_running_are_cancelled_and_the_loop_thread_exits():
    left = []
    wf = Workflow("demo")

    @wf.step
    async def a():
        left.append(asyncio.create_task(asyncio.sleep(60)))

    run, _ = execute(wf)

    assert run.status is RunStatus.COMPLETED
    assert left[0].cancelled()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and any(t.name == f"clabe-run-{run.id}-loop" for t in threading.enumerate()):
        time.sleep(0.01)
    assert not any(t.name == f"clabe-run-{run.id}-loop" for t in threading.enumerate())


def test_an_async_step_raising_system_exit_fails_the_run():
    wf = Workflow("demo")

    @wf.step
    async def a():
        await asyncio.sleep(0)
        raise SystemExit("boom")

    run, _ = execute(wf)
    assert (run.status, run.reason) == (RunStatus.FAILED, "boom")


def test_an_async_step_raising_keyboard_interrupt_pauses_on_the_failure_pick():
    wf = Workflow("demo")

    @wf.step
    async def a():
        await asyncio.sleep(0)
        raise KeyboardInterrupt

    run, client = execute(wf, ["abort"])
    assert client.prompts[0].kind == "pick"
    assert run.status is RunStatus.FAILED  # aborting after a failure fails the run
    assert isinstance(run.error, KeyboardInterrupt)


def test_a_background_task_raising_system_exit_does_not_stop_later_steps():
    wf = Workflow("demo")

    @wf.step
    async def a():
        async def background():
            await asyncio.sleep(0.01)
            raise SystemExit("background")

        return asyncio.create_task(background())

    @wf.step
    async def b(a):
        await asyncio.sleep(0.05)
        return 1

    run, _ = execute(wf)
    assert run.status is RunStatus.COMPLETED
    assert run.output("b") == 1


def test_a_step_waiting_on_a_dead_event_loop_fails():
    wf = Workflow("demo")

    @wf.step
    async def a():
        asyncio.get_running_loop().stop()  # the loop thread exits; this step never finishes
        await asyncio.sleep(60)

    tasks_at_the_pick = []

    def record_task_then_abort(run, event):
        tasks_at_the_pick.append(run._task)
        return run.answer(event.prompt_id, "abort", client="test")

    run, client = execute(wf, [record_task_then_abort])
    assert "stopped unexpectedly" in client.prompts[0].spec["label"]
    assert tasks_at_the_pick == [None]  # the dead task is no longer treated as interruptible
    assert run.status is RunStatus.FAILED
    assert isinstance(run.error, RuntimeError)


def test_an_exception_escaping_the_loop_after_a_stop_request_does_not_restart_it():
    run = Run(Workflow("demo"))
    run._start_loop()
    loop, thread = run._loop, run._loop_thread

    def stop_then_escape():
        run._loop_stopping = True  # what _stop_loop sets before asking the loop to stop
        loop.stop()
        raise SystemExit("escapes after the stop request")  # run_forever then forgets the stop request

    loop.call_soon_threadsafe(stop_then_escape)
    thread.join(2)
    try:
        assert not thread.is_alive()
    finally:
        if not thread.is_alive():
            loop.close()


def test_steps_see_the_starting_threads_context():
    _VAR.set("outer")
    wf = Workflow("demo")
    wf.step(lambda: _VAR.get(), name="a")
    run, _ = execute(wf)
    assert run.output("a") == "outer"


def test_events_are_persisted(tmp_path):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run, _ = execute(wf, record_dir=tmp_path)
    assert EventLog.read(tmp_path / "events.jsonl") == run.events.since(0)


def test_a_run_whose_events_file_cannot_be_written_still_completes(tmp_path):
    (tmp_path / "events.jsonl").mkdir()  # a directory where the file should be: every write fails
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run, _ = execute(wf, record_dir=tmp_path)
    assert run.status is RunStatus.COMPLETED
    assert isinstance(run.events.since(0)[-1], RunFinished)


def test_a_run_does_not_see_steps_added_to_its_workflow_after_it_was_created():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    wf.step(lambda a: a + 1, name="b")
    run = Run(wf)
    wf.insert_after("a", lambda a: a * 10, name="late")

    run.start()
    assert run.wait(5)

    assert run.status is RunStatus.COMPLETED
    assert [e.step for e in events_of(run, StepStarted)] == ["a", "b"]
    assert [s.name for s in run.state().steps] == ["a", "b"]
    assert run.output("b") == 2


def test_a_run_finishes_even_if_recording_its_end_fails():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)
    append = run.events.append

    def failing_append(event):
        if isinstance(event, RunFinished):
            raise OSError("disk full")
        return append(event)

    run.events.append = failing_append
    run.start()
    assert run.wait(5)
    assert run.status is RunStatus.COMPLETED


class _Unprintable:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


def test_an_output_that_cannot_be_summarized_still_completes():
    wf = Workflow("demo")
    value = _Unprintable()
    wf.step(lambda: value, name="a")
    run, _ = execute(wf)
    assert run.status is RunStatus.COMPLETED
    assert run.output("a") is value
    assert events_of(run, StepFinished)[0].summary == "<_Unprintable>"


def test_a_run_starts_once():
    wf = Workflow("demo")
    run, _ = execute(wf)
    with pytest.raises(RuntimeError):
        run.start()
