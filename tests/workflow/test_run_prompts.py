import contextlib
import threading

import pytest
from pydantic import BaseModel, SecretStr

from clabe.ui import (
    ConfirmRequest,
    FormRequest,
    Frontend,
    MessageLevel,
    NumberRequest,
    PathRequest,
    PromptCancelledError,
    TextRequest,
)
from clabe.workflow import Ack, Rejected, Run, RunFrontend, Workflow
from clabe.workflow.events import EventLog, Header, Notify, PromptClosed, PromptOpened

from ._helpers import events_of, execute


def test_prompt_round_trip():
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="Name")), name="name")

    run, _ = execute(wf, ["mouse"])

    assert run.output("name") == "mouse"
    opened = events_of(run, PromptOpened)[0]
    assert (opened.kind, opened.step, opened.spec["label"]) == ("text", "name", "Name")
    assert opened.answer_schema == {"type": "string"}
    closed = events_of(run, PromptClosed)[0]
    assert (closed.outcome, closed.answered_by) == ("answered", "test")
    assert run.answer(opened.prompt_id, "late") == Rejected("This prompt is no longer open.")


class _Rig(BaseModel):
    name: str
    trials: int = 1
    token: SecretStr = SecretStr("hidden")


def test_prompt_closed_records_the_accepted_answer(tmp_path):
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(TextRequest(label="Name")), name="name")
    wf.step(lambda run: run.ask(FormRequest(model=_Rig)), name="rig")

    @wf.step
    def cancelled(run):
        with contextlib.suppress(PromptCancelledError):
            run.ask(ConfirmRequest(label="?"))

    run, _ = execute(
        wf, ["mouse", {"name": "r1", "trials": 3}, lambda run, event: run.cancel_prompt()], record_dir=tmp_path
    )

    expected = ["mouse", {"name": "r1", "trials": 3, "token": "**********"}, None]
    assert [e.answer for e in events_of(run, PromptClosed)] == expected
    persisted = [e for e in EventLog.read(tmp_path / "events.jsonl") if isinstance(e, PromptClosed)]
    assert [e.answer for e in persisted] == expected


def test_invalid_answer_is_rejected_and_the_prompt_stays_open():
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(NumberRequest(label="n")), name="n")

    def answer_twice(run, event):
        return run.answer(event.prompt_id, "abc"), run.answer(event.prompt_id, "2")

    run, client = execute(wf, [answer_twice])

    first, second = client.results[0]
    assert isinstance(first, Rejected) and "number" in first.reason
    assert second == Ack()
    assert run.output("n") == 2.0


def test_cancel_prompt_raises_in_the_step():
    wf = Workflow("demo")

    @wf.step
    def a(run):
        try:
            run.ask(ConfirmRequest(label="?"))
        except PromptCancelledError:
            return "cancelled"

    run, _ = execute(wf, [lambda run, event: run.cancel_prompt()])

    assert run.output("a") == "cancelled"
    assert events_of(run, PromptClosed)[0].outcome == "cancelled"


def test_a_prompt_still_open_when_the_run_finishes_is_cancelled():
    outcome = []
    opened = threading.Event()
    wf = Workflow("demo")

    @wf.step
    def a(run):
        def stray():
            try:
                run.ask(ConfirmRequest(label="?"))
            except PromptCancelledError:
                outcome.append("cancelled")

        run.events.subscribe(lambda event: opened.set() if isinstance(event, PromptOpened) else None)
        thread = threading.Thread(target=stray, daemon=True)
        thread.start()
        assert opened.wait(5)
        return thread

    run = Run(wf)
    run.start()
    assert run.wait(5)
    run.output("a").join(5)

    assert outcome == ["cancelled"]
    assert events_of(run, PromptClosed)[0].outcome == "cancelled"
    assert not run.is_prompt_open(events_of(run, PromptOpened)[0].prompt_id)


def _ask_with_timeout(run, timeout: float = 5.0):
    """Calls ``run.ask`` on a thread so a regression fails the test instead of hanging it."""
    raised = []

    def ask():
        try:
            run.ask(ConfirmRequest(label="?"))
        except PromptCancelledError as exc:
            raised.append(exc)

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        run.cancel_prompt()
        pytest.fail("ask() blocked")
    return raised


def test_ask_on_a_finished_run_raises():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run, _ = execute(wf)

    assert len(_ask_with_timeout(run)) == 1
    assert events_of(run, PromptOpened) == []


def test_ask_on_a_run_that_never_started_raises():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")

    assert len(_ask_with_timeout(Run(wf))) == 1


def test_a_subscriber_may_answer_on_the_publishing_thread():
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(ConfirmRequest(label="?")), name="a")
    run = Run(wf)
    results = []

    def answer_now(event):
        if isinstance(event, PromptOpened):
            results.append(run.answer(event.prompt_id, True))

    run.events.subscribe(answer_now)
    run.start()
    assert run.wait(5)

    assert results == [Ack()]
    assert run.output("a") is True
    assert events_of(run, PromptOpened)[0].seq > 0


def test_an_answer_that_breaks_the_validator_is_rejected_and_the_prompt_stays_open():
    wf = Workflow("demo")
    wf.step(lambda run: run.ask(PathRequest(label="p")), name="p")

    def answer_badly_then_cancel(run, event):
        rejected, still_open = run.answer(event.prompt_id, 5), run.is_prompt_open(event.prompt_id)
        run.answer(event.prompt_id, None)
        return rejected, still_open

    run, client = execute(wf, [answer_badly_then_cancel])

    rejected, still_open = client.results[0]
    assert isinstance(rejected, Rejected) and rejected.reason.startswith("Invalid answer:")
    assert still_open
    assert run.output("p") is None


def test_run_frontend_routes_messages_and_prompts_through_the_run():
    wf = Workflow("demo")

    @wf.step
    def a(run):
        ui = RunFrontend(run)
        ui.notify("hello", MessageLevel.WARNING)
        ui.header("Title")
        return isinstance(ui, Frontend), ui.prompt_confirm(ConfirmRequest(label="ok?"))

    run, _ = execute(wf, [True])

    assert run.output("a") == (True, True)
    note = events_of(run, Notify)[0]
    assert (note.message, note.level) == ("hello", "warning")
    assert events_of(run, Header)[0].text == "Title"
