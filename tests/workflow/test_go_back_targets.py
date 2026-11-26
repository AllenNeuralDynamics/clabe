"""GoBack by a step's function, and the GoBack targets `clabe check` can verify without running anything."""

from clabe import steps
from clabe.ui import ConfirmRequest
from clabe.workflow import GoBack, RunStatus, Workflow, check_workflow

from ._helpers import execute


def pick_subject() -> str:
    return "mouse"


def test_go_back_to_a_step_by_its_function_even_under_another_name():
    calls = []
    wf = Workflow("demo")

    def subject_step() -> str:
        calls.append("subject")
        return "mouse"

    wf.step(subject_step, name="subject")  # the function's name is not the step's

    @wf.step
    def confirm(run, subject: str) -> None:
        calls.append("confirm")
        if not run.ask(ConfirmRequest(label="ok?")):
            raise GoBack(subject_step)

    run, _ = execute(wf, [False, True])
    assert run.status is RunStatus.COMPLETED
    assert calls == ["subject", "confirm", "subject", "confirm"]


def test_go_back_to_a_function_that_is_no_step_fails_the_step_with_a_reason():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")

    @wf.step
    def b(a: int) -> None:
        raise GoBack(pick_subject)

    run, client = execute(wf, ["abort"])
    assert run.status is RunStatus.FAILED
    assert "pick_subject: it is not a step of 'demo'" in client.prompts[0].spec["label"]


def test_a_function_used_by_two_steps_needs_a_name():
    wf = Workflow("demo")
    wf.step(pick_subject, name="first")
    wf.step(pick_subject, name="second")
    assert wf.step_named_by(pick_subject)[1] == (
        "Cannot go back to pick_subject: steps 'first', 'second' run it; give a name."
    )
    assert wf.step_named_by(wf.get("first")) == (None, wf.step_named_by(pick_subject)[1])
    assert wf.step_named_by("first") == ("first", None)


def test_the_check_reports_go_back_targets_that_are_no_earlier_step():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="session")
    wf.step(steps.review("session", back_to="no-such-step"))  # a name that is no step

    @wf.step
    def confirm(session: int) -> None:
        if not session:
            raise GoBack("register")  # comes later
        raise GoBack(to="session")  # fine

    wf.step(lambda: None, name="register")
    messages = [(i.step, i.message) for i in check_workflow(wf) if i.severity == "error"]
    assert messages == [
        ("review", "goes back to 'no-such-step', which is not a step of this workflow."),
        ("confirm", "goes back to 'register', which comes after it."),
    ]
