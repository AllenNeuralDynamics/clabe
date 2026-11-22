"""Conditional steps (``when=``): skipped when their condition does not hold, re-evaluated on going back."""

import pytest

from clabe.ui import ConfirmRequest, NumberRequest
from clabe.workflow import GoBack, RunStatus, StepStatus, Workflow, WorkflowDefinitionError, check_workflow
from clabe.workflow.events import StepsInvalidated, StepSkipped

from ._helpers import events_of, execute


def _calibration(calls: list[str]) -> Workflow:
    wf = Workflow("demo")

    @wf.step
    def mode(run) -> int:
        calls.append("mode")
        return int(run.ask(NumberRequest(label="mode")))

    @wf.step(when=lambda mode: mode == 1)
    def calibrate() -> float:
        calls.append("calibrate")
        return 1.0

    @wf.step(when=lambda mode: mode != 1)
    def load() -> float:
        calls.append("load")
        return 2.0

    @wf.step
    def acquire(run, calibrate: float | None, load: float | None) -> float:
        calls.append("acquire")
        if not run.ask(ConfirmRequest(label="ok?")):
            raise GoBack("mode")
        return calibrate if calibrate is not None else load

    return wf


def test_only_the_step_whose_condition_holds_runs():
    calls = []
    run, _ = execute(_calibration(calls), [1, True])
    assert calls == ["mode", "calibrate", "acquire"]
    assert run.status is RunStatus.COMPLETED
    assert run.output("load") is None and run.output("acquire") == 1.0
    skipped = events_of(run, StepSkipped)
    assert [(e.step, e.reason) for e in skipped] == [("load", "condition")]
    state = {s.name: s for s in run.state().steps}
    assert state["load"].status is StepStatus.SKIPPED and state["load"].skip_reason == "condition"
    assert state["load"].conditional and not state["mode"].conditional


def test_going_back_evaluates_the_conditions_again():
    calls = []
    run, _ = execute(_calibration(calls), [1, False, 2, True])
    assert calls == ["mode", "calibrate", "acquire", "mode", "load", "acquire"]
    assert run.output("calibrate") is None and run.output("acquire") == 2.0
    assert events_of(run, StepsInvalidated)[0].steps == [
        "mode",
        "calibrate",
        "load",
    ]  # acquire raised GoBack: already pending
    state = {s.name: s for s in run.state().steps}
    assert state["calibrate"].skip_reason == "condition" and state["load"].skip_reason is None


def test_a_condition_is_checked_before_the_optional_question():
    wf = Workflow("demo")
    wf.step(lambda: 0, name="n")
    wf.step(lambda: "qc", name="qc", when=lambda n: n > 0, optional="Run QC?")
    wf.step(lambda: "report", name="report", when=lambda n: n == 0, optional="Write a report?")
    run, client = execute(wf, [False])
    assert [p.spec["label"] for p in client.prompts] == ["Write a report?"]
    assert [(e.step, e.reason) for e in events_of(run, StepSkipped)] == [("qc", "condition"), ("report", "declined")]


def test_a_failing_condition_pauses_the_run_like_a_failing_step():
    attempts = []

    def flaky(n):
        attempts.append(n)
        if len(attempts) == 1:
            raise ValueError("no such mode")
        return True

    wf = Workflow("demo")
    wf.step(lambda: 3, name="n")
    wf.step(lambda: "ran", name="b", when=flaky)
    run, client = execute(wf, ["retry"])
    assert "The condition of B failed: no such mode" in client.prompts[0].spec["label"]
    assert run.output("b") == "ran" and attempts == [3, 3]


def test_conditions_are_wired_like_steps():
    wf = Workflow("demo")
    with pytest.raises(WorkflowDefinitionError, match="needs 'mode'"):
        wf.step(lambda: 1, name="b", when=lambda mode: mode)

    async def is_set(mode):
        return mode

    wf.step(lambda: 1, name="mode")
    with pytest.raises(WorkflowDefinitionError, match="plain"):
        wf.step(lambda: 1, name="b", when=is_set)
    with pytest.raises(WorkflowDefinitionError, match="name each input"):
        wf.step(lambda: 1, name="b", when=lambda *a: True)
    wf.step(lambda run: 1, name="b", when=lambda mode, run: mode)
    assert wf.get("b").deps == ("mode",)
    assert wf.dependents("mode") == ["b"]


def test_the_static_check_treats_a_conditional_output_as_optional():
    wf = Workflow("demo")

    @wf.step
    def mode() -> int:
        return 1

    @wf.step(when=lambda mode: mode == 1)
    def calibrate() -> float:
        return 1.0

    @wf.step
    def acquire(calibrate: float) -> None: ...

    issues = check_workflow(wf, strict=True)
    expected = (
        "step 'calibrate' is optional, conditional or on_error='warn', so 'calibrate' may be None; "
        "annotate it as float | None."
    )
    assert [i.message for i in issues if i.severity == "error"] == [expected]
    assert not [i for i in issues if i.step == "calibrate"]  # an unannotated lambda condition is fine


def test_replace_keeps_the_condition():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="mode")
    wf.step(lambda: 1, name="b", when=lambda mode: mode)
    other = wf.variant("other").replace("b", lambda: 2)
    assert other.get("b").when is wf.get("b").when
