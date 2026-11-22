"""Forward and prefill: a prompt answered before remembers its answer; Back goes to the previous step."""

import pydantic

from clabe import ui
from clabe.ui import NavigationRequested, TextRequest
from clabe.workflow import Ack, LocalClient, Rejected, Run, RunStatus, Workflow
from clabe.workflow._local import prefill
from clabe.workflow.events import PromptClosed, PromptOpened
from tests.conftest import MockClient

from ._helpers import events_of, execute


def _two_steps() -> Workflow:
    wf = Workflow("demo")

    @wf.step
    def subject():
        return ui.text("Subject", field="subject")

    @wf.step
    def check(subject):
        return ui.confirm(f"Is {subject} right?", field="check")

    return wf


def _back(run, event):
    return run.back()


def _forward(run, event):
    return run.forward(client="test")


def test_a_prompt_answered_before_offers_its_answer_and_forward_repeats_it():
    run, client = execute(_two_steps(), ["mouse-7", _back, _forward, True])

    first, second, again = client.prompts[0], client.prompts[1], client.prompts[2]
    assert (first.can_forward, first.previous_answer) == (False, None)
    assert (second.can_forward, second.previous_answer) == (False, None)
    assert again.step == "subject" and again.can_forward and again.previous_answer == "mouse-7"
    assert client.results[1] == Ack() and client.results[2] == Ack()
    assert run.status is RunStatus.COMPLETED and run.output("subject") == "mouse-7"
    assert events_of(run, PromptClosed)[-2].answered_by == "test"


def _then_answer(command, value):
    """Runs ``command`` on the open prompt, then answers it with ``value``; returns the command's result."""

    def act(run, event):
        result = command(run, event)
        run.answer(event.prompt_id, value, client="test")
        return result

    return act


def test_forward_without_an_earlier_answer_is_rejected():
    run, client = execute(_two_steps(), [_then_answer(_forward, "m"), True])
    assert isinstance(client.results[0], Rejected)
    assert run.output("subject") == "m"


def test_forward_answers_through_validation():
    wf = Workflow("demo")
    limit = {"max": 10}

    @wf.step
    def a():
        return ui.number("Trials", field="trials", maximum=limit["max"])

    @wf.step
    def b(a):
        limit["max"] = 5
        return ui.confirm("ok?", field="ok")

    def forward_then_answer(run, event):
        result = run.forward(client="test")
        run.answer(event.prompt_id, 3, client="test")
        return result

    run, client = execute(wf, [8, _back, forward_then_answer, True])
    assert isinstance(client.results[2], Rejected)
    assert run.output("a") == 3


def test_secret_answers_are_never_remembered():
    class Login(pydantic.BaseModel):
        password: pydantic.SecretStr

    wf = Workflow("demo")

    @wf.step
    def a():
        return ui.field(Login, "password")

    @wf.step
    def b(a):
        return ui.confirm("ok?", field="ok")

    _run, client = execute(wf, ["hunter2", _back, "again", True])
    assert not client.prompts[2].can_forward and client.prompts[2].previous_answer is None


def test_a_failure_question_is_never_forwarded():
    attempts = []
    wf = Workflow("demo")

    @wf.step
    def a():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("boom")

    _run, client = execute(wf, ["retry", "retry"])
    failure_prompts = [p for p in client.prompts if p.spec.get("field") == "a.failed"]
    assert len(failure_prompts) == 2 and not any(p.can_forward for p in failure_prompts)


def test_state_exposes_back_target_and_the_open_prompt_previous_answer():
    states = []

    def look(run, event):
        states.append(run.state())
        return run.forward(client="test")

    execute(_two_steps(), ["x", _back, look, True])
    assert states[0].back_target is None  # subject is the first step
    assert states[0].prompt is not None and states[0].prompt.previous_answer == "x"

    seen = []
    execute(
        _two_steps(),
        ["x", lambda run, event: seen.append(run.state().back_target) or run.answer(event.prompt_id, True)],
    )
    assert seen == ["subject"]


def test_back_with_no_earlier_step_is_rejected():
    _run, client = execute(_two_steps(), [_then_answer(_back, "m"), True])
    assert isinstance(client.results[0], Rejected)


def test_prefill_sets_defaults_of_each_kind():
    assert prefill(TextRequest(label="t"), "x").default == "x"
    assert prefill(ui.ConfirmRequest(label="c"), True).default is True
    assert prefill(ui.NumberRequest(label="n"), 3).default == 3.0
    assert prefill(ui.PickRequest(label="p", options=["a", "b"]), "b").default == "b"
    assert prefill(ui.PickRequest(label="p", options=["a", "b"]), "z").default is None  # does not fit: unchanged
    group = prefill(ui.GroupRequest(items={"notes": TextRequest(label="Notes")}), {"notes": "hi"})
    assert group.items["notes"].default == "hi"

    class Model(pydantic.BaseModel):
        x: int = 0

    assert prefill(ui.FormRequest(model=Model), {"x": 4}).initial == Model(x=4)


def test_local_client_prefills_and_navigates():
    client = MockClient(
        {"text": ["mouse-7", NavigationRequested("forward")], "confirm": [NavigationRequested("back"), True]}
    )
    run = Run(_two_steps())
    LocalClient(run, client).drive()

    assert run.status is RunStatus.COMPLETED and run.output("subject") == "mouse-7"
    asked_texts = [request for request in client.asked if request.prompt_kind == "text"]
    assert asked_texts[0].default is None and asked_texts[1].default == "mouse-7"  # prefilled on the second visit
    opened = [e for e in events_of(run, PromptOpened) if e.step == "subject"]
    assert len(opened) == 2 and opened[1].can_forward


def test_local_client_reports_a_refused_navigation():
    client = MockClient({"text": [NavigationRequested("forward"), "m"], "confirm": True})
    run = Run(_two_steps())
    LocalClient(run, client).drive()
    assert run.status is RunStatus.COMPLETED
    assert any("no earlier answer" in message for message in client.messages)
