import threading

from clabe import ui
from clabe.ui import MessageLevel
from clabe.workflow import Run, Workflow
from clabe.workflow.events import Header, Notify, PromptOpened, RunFinished, RunStarted

from ._helpers import events_of, execute


def test_inside_a_run_ui_helpers_open_run_prompts():
    wf = Workflow("demo")
    wf.step(lambda: ui.confirm("Ready?", default=True), name="ready")
    wf.step(lambda: ui.text("Name"), name="name")

    run, _ = execute(wf, [False, "mouse"])

    assert (run.output("ready"), run.output("name")) == (False, "mouse")
    opened = events_of(run, PromptOpened)
    assert [(e.kind, e.step, e.spec["label"]) for e in opened] == [
        ("confirm", "ready", "Ready?"),
        ("text", "name", "Name"),
    ]


def test_inside_a_run_ui_field_opens_the_resolved_prompt():
    from pydantic import BaseModel

    class Rig(BaseModel):
        trials: int = 1

    wf = Workflow("demo")
    wf.step(lambda: ui.field(Rig, "trials"), name="trials")

    run, _ = execute(wf, ["7"])

    assert run.output("trials") == 7
    assert events_of(run, PromptOpened)[0].kind == "text"


def test_inside_a_run_notify_and_header_are_recorded():
    wf = Workflow("demo")

    @wf.step
    def a():
        ui.notify("hello", MessageLevel.WARNING)
        ui.header("Title")

    run, _ = execute(wf)

    note = events_of(run, Notify)[0]
    assert (note.message, note.level) == ("hello", "warning")
    assert events_of(run, Header)[0].text == "Title"


def test_threads_started_by_a_step_reach_the_run():
    wf = Workflow("demo")

    @wf.step
    def a():
        seen = []
        thread = threading.Thread(target=lambda: seen.append(ui.current_session()))
        thread.start()
        thread.join(5)
        return seen

    run, _ = execute(wf)

    assert run.output("a") == [run]


def test_the_run_is_bound_from_run_started_to_run_finished_then_unbound():
    seen = {}
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)

    def on_event(event):
        if isinstance(event, RunStarted | RunFinished):
            seen[type(event).__name__] = ui.current_session()

    run.events.subscribe(on_event)
    run.start()
    assert run.wait(5)

    assert seen == {"RunStarted": run, "RunFinished": run}
    assert ui.current_session() is None


def test_the_run_is_bound_as_soon_as_start_returns():
    release = threading.Event()
    wf = Workflow("demo")
    wf.step(lambda: release.wait(5), name="a")
    run = Run(wf)

    run.start()
    try:
        assert ui.current_session() is run
    finally:
        release.set()
    assert run.wait(5)
    assert ui.current_session() is None


def test_the_run_restores_the_previous_session():
    class Outer:
        def ask(self, request):
            return None

        def notify(self, message, level=MessageLevel.INFO):
            pass

        def header(self, text):
            pass

    outer = Outer()
    wf = Workflow("demo")
    wf.step(lambda: ui.current_session(), name="a")

    with ui.bind(outer):
        run, _ = execute(wf)
        assert ui.current_session() is outer

    assert run.output("a") is run


def test_a_crashing_run_still_unbinds_and_finishes(monkeypatch):
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    run = Run(wf)

    def crash(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Run, "_drive", crash)
    run.start()

    assert run.wait(5)
    assert run.status.value == "failed"
    assert ui.current_session() is None
