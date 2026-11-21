import contextlib
from unittest.mock import MagicMock

from clabe.runnable import get_activity_indicator
from clabe.ui import ConfirmRequest
from clabe.workflow import Workflow
from clabe.workflow.events import ActivityEnded, ActivityStarted

from ._helpers import events_of, execute


def test_activities_during_a_step_become_events():
    wf = Workflow("demo")

    @wf.step
    def a():
        with get_activity_indicator().activity("Running Bonsai"):
            pass

    run, _ = execute(wf)

    started = events_of(run, ActivityStarted)[0]
    assert (started.description, started.step) == ("Running Bonsai", "a")
    assert events_of(run, ActivityEnded)[0].activity_id == started.activity_id


def test_activities_stop_being_recorded_after_the_run():
    wf = Workflow("demo")
    run, _ = execute(wf)
    before = run.events.last_seq
    with get_activity_indicator().activity("later"):
        pass
    assert run.events.last_seq == before


def test_activities_stop_being_recorded_after_a_crashed_run():
    wf = Workflow("demo")

    @wf.step
    def a():
        raise SystemExit("boom")  # not an Exception: escapes the step and crashes the run

    run, _ = execute(wf)
    assert run.status == "failed"
    before = run.events.last_seq
    with get_activity_indicator().activity("later"):
        pass
    assert run.events.last_seq == before


def test_state_snapshot_reports_steps_prompt_and_locks():
    wf = Workflow("demo")
    wf.step(lambda: "x", name="a", barrier=True)
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="b")
    captured = {}

    def snapshot_then_answer(run, event):
        captured["state"] = run.state()
        return run.answer(event.prompt_id, True)

    execute(wf, [snapshot_then_answer])

    state = captured["state"]
    assert [(s.name, s.status, s.locked, s.summary) for s in state.steps] == [
        ("a", "done", True, "'x'"),
        ("b", "running", False, None),
    ]
    assert (state.current_step, state.prompt.kind, state.status) == ("b", "confirm", "running")
    assert state.last_seq == state.prompt.seq


def test_state_reuses_the_summary_recorded_when_the_step_finished(monkeypatch):
    wf = Workflow("demo")
    wf.step(lambda: "x", name="a")
    run, _ = execute(wf)

    def fail(value, limit=500):
        raise AssertionError("state() must not re-summarize outputs")

    monkeypatch.setattr("clabe.workflow._run._summarize", fail)
    assert [s.summary for s in run.state().steps] == ["'x'"]


def test_state_forgets_summaries_of_steps_that_go_back():
    wf = Workflow("demo")
    wf.step(lambda: "x", name="a")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="b")
    captured = {}

    def go_back(run, event):
        return run.go_back("a")

    def snapshot_then_answer(run, event):
        captured["state"] = run.state()
        return run.answer(event.prompt_id, True)

    execute(wf, [go_back, snapshot_then_answer])
    # a ran again, so its summary is back; the snapshot was taken during b's second prompt
    assert [s.summary for s in captured["state"].steps] == ["'x'", None]


def test_each_step_runs_in_a_span(monkeypatch):
    names = []

    @contextlib.contextmanager
    def fake_span(name, attributes=None):
        names.append(name)
        yield MagicMock()

    monkeypatch.setattr("clabe.workflow._run.span", fake_span)
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")
    wf.step(lambda: None, name="b")
    execute(wf)
    assert names == ["clabe.step.a", "clabe.step.b"]
