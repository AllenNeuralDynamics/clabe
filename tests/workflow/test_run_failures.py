from clabe.workflow import GoBack, Rejected, Run, RunStatus, StepStatus, Workflow
from clabe.workflow._run import _Command
from clabe.workflow.events import Notify, RunFinished, StepFailed, StepSkipped

from ._helpers import ScriptedClient, events_of, execute


def test_a_failure_pauses_and_can_be_retried():
    attempts = []
    wf = Workflow("demo")

    @wf.step
    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return "ok"

    run, client = execute(wf, ["retry"])

    assert run.status is RunStatus.COMPLETED
    assert run.output("flaky") == "ok"
    failed = events_of(run, StepFailed)[0]
    assert failed.error == "boom" and "RuntimeError" in failed.traceback
    assert not failed.warn_only
    assert [o["value"] for o in client.prompts[0].spec["options"]] == ["retry", "abort"]


def test_abort_after_a_failure_fails_the_run():
    wf = Workflow("demo")

    @wf.step
    def broken():
        raise RuntimeError("boom")

    run, _ = execute(wf, ["abort"])

    assert run.status is RunStatus.FAILED
    assert isinstance(run.error, RuntimeError)
    assert run._steps["broken"] is StepStatus.FAILED


def test_go_back_after_a_failure():
    calls = []
    wf = Workflow("demo")
    wf.step(lambda: calls.append("a"), name="a")

    @wf.step
    def b():
        calls.append("b")
        if calls.count("b") == 1:
            raise RuntimeError("boom")

    run, client = execute(wf, ["back"])

    assert run.status is RunStatus.COMPLETED
    assert calls == ["a", "b", "a", "b"]
    assert [o["value"] for o in client.prompts[0].spec["options"]] == ["retry", "back", "abort"]


def test_warn_steps_notify_and_continue_with_none():
    wf = Workflow("demo")

    @wf.step(on_error="warn")
    def waterlog():
        raise RuntimeError("offline")

    wf.step(lambda waterlog: waterlog, name="after")

    run, client = execute(wf)

    assert run.status is RunStatus.COMPLETED
    assert run.output("after") is None
    assert run._steps["waterlog"] is StepStatus.FAILED
    assert events_of(run, StepFailed)[0].warn_only
    assert "offline" in events_of(run, Notify)[0].message
    assert client.prompts == []


def test_optional_steps_ask_first():
    ran = []
    wf = Workflow("demo")

    @wf.step(optional="Generate a QC report?")
    def qc():
        ran.append("qc")
        return "report"

    wf.step(lambda qc: qc, name="done")

    run, client = execute(wf, [False])
    assert ran == [] and run.output("done") is None
    assert (client.prompts[0].kind, client.prompts[0].spec["label"]) == ("confirm", "Generate a QC report?")
    assert events_of(run, StepSkipped)[0].step == "qc"
    assert run._steps["qc"] is StepStatus.SKIPPED

    run, _ = execute(wf, [True])
    assert ran == ["qc"] and run.output("done") == "report"
    assert events_of(run, StepSkipped) == []


def test_an_optional_question_can_suggest_no():
    wf = Workflow("demo")
    wf.step(lambda: "report", name="qc", optional="Generate a QC report?", optional_default=False)
    _, client = execute(wf, [False])
    assert client.prompts[0].spec["default"] is False
    assert wf.get("qc").options()["optional_default"] is False
    _, client = execute(wf.variant("other").replace("qc", lambda: "other"), [False])
    assert client.prompts[0].spec["default"] is False  # replace keeps it


def test_a_rejected_go_back_pauses_and_can_be_retried():
    attempts = []
    wf = Workflow("demo")

    @wf.step
    def first():
        attempts.append(1)
        if len(attempts) == 1:
            raise GoBack()
        return "ok"

    run, client = execute(wf, ["retry"])

    assert run.status is RunStatus.COMPLETED
    assert run.output("first") == "ok"
    assert "no earlier step" in events_of(run, StepFailed)[0].error
    assert client.prompts[0].spec["options"][0]["value"] == "retry"


def test_go_back_to_the_step_itself_runs_it_again():
    attempts = []
    wf = Workflow("demo")

    @wf.step
    def first():
        attempts.append(1)
        if len(attempts) == 1:
            raise GoBack("first")
        return "ok"

    run, client = execute(wf)

    assert run.status is RunStatus.COMPLETED
    assert run.output("first") == "ok"
    assert len(attempts) == 2
    assert events_of(run, StepFailed) == [] and client.prompts == []


def test_go_back_is_rejected_while_a_failed_run_is_finishing():
    wf = Workflow("demo")
    wf.step(lambda: None, name="a")

    @wf.step
    def broken():
        raise RuntimeError("boom")

    run = Run(wf)
    results = []
    run.events.subscribe(lambda event: isinstance(event, RunFinished) and results.append(run.go_back("a")))
    ScriptedClient(run, ["abort"])
    run.start()
    assert run.wait(5)
    assert run.status is RunStatus.FAILED
    assert results == [Rejected("The run is finishing.")]


def test_a_go_back_locked_by_a_barrier_meanwhile_is_dropped_with_the_step_title():
    wf = Workflow("demo")
    wf.step(lambda: None, name="pick_session")
    wf.step(lambda: None, name="register", barrier=True)
    run = Run(wf)
    run._barrier = 1  # "register" completed after the go back was accepted
    run._pending = _Command("back", target="pick_session")

    run._apply_pending()

    assert "Cannot go back to Pick session: a barrier has been passed." in [e.message for e in events_of(run, Notify)]
