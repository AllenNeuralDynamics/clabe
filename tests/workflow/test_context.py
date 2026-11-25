"""What a run is about (its context): the workflow, the host, the rig, the subject and the experimenters."""

import dataclasses
import socket

import pytest

from clabe.workflow import LocalClient, Run, RunContext, Workflow
from clabe.workflow._context import RIG_NAME_ENV_VAR, describe_run
from clabe.workflow.events import ContextChanged

from ..conftest import MockClient
from ._helpers import events_of, execute


@dataclasses.dataclass
class _Session:
    subject: str
    experimenter: list[str]


@dataclasses.dataclass
class _Rig:
    rig_name: str


@pytest.fixture(autouse=True)
def no_rig_env(monkeypatch):
    monkeypatch.delenv(RIG_NAME_ENV_VAR, raising=False)


def _workflow() -> Workflow:
    wf = Workflow("vr-foraging")

    @wf.step
    def rig() -> _Rig:
        return _Rig("FROM-A-STEP")

    @wf.step
    def session() -> _Session:
        return _Session("123456", ["Ana", "Bo"])

    return wf


def test_the_context_is_known_from_the_start_and_grows_as_steps_finish(monkeypatch):
    monkeypatch.setenv(RIG_NAME_ENV_VAR, "RIG-7")
    run, _ = execute(_workflow())
    contexts = events_of(run, ContextChanged)
    assert [(c.rig, c.subject, c.experimenter) for c in contexts] == [
        ("RIG-7", None, []),
        ("RIG-7", "123456", ["Ana", "Bo"]),
    ]
    assert {(c.workflow, c.hostname) for c in contexts} == {("vr-foraging", socket.gethostname())}
    assert run.state().context == RunContext(
        workflow="vr-foraging", hostname=socket.gethostname(), rig="RIG-7", subject="123456", experimenter=["Ana", "Bo"]
    )


def test_an_unchanged_context_is_not_recorded_again():
    wf = Workflow("demo")
    wf.step(lambda: 1, name="a")
    wf.step(lambda: "text", name="b")
    run, _ = execute(wf)
    assert len(events_of(run, ContextChanged)) == 1


def test_only_the_environment_names_the_rig(monkeypatch):
    run, _ = execute(_workflow())  # its rig step returns a rig_name: not used
    assert run.state().context.rig is None
    monkeypatch.setenv(RIG_NAME_ENV_VAR, "AIND-RIG-1")
    run, _ = execute(_workflow())
    assert run.state().context.rig == "AIND-RIG-1"


def test_the_launcher_session_wins_over_step_outputs():
    class _Launcher:
        session = _Session("999", ["Cy"])

    wf = Workflow("demo")
    run = Run(wf, launcher=_Launcher())
    context = describe_run(run, [_Session("123", ["Ana"])])
    assert (context.subject, context.experimenter) == ("999", ["Cy"])


def test_a_launcher_without_a_session_leaves_the_outputs():
    class _Launcher:
        @property
        def session(self):
            raise ValueError("Session is not set.")

    run = Run(Workflow("demo"), launcher=_Launcher())
    context = describe_run(run, [_Session("123", ["Ana"]), object(), {"subject": "ignored"}])
    assert (context.subject, context.experimenter) == ("123", ["Ana"])


def test_local_client_hands_the_context_to_a_client_that_shows_it():
    class _Client(MockClient):
        def __init__(self):
            super().__init__()
            self.contexts = []

        def set_context(self, context):
            self.contexts.append(context)

    client = _Client()
    run = Run(_workflow())
    LocalClient(run, client).drive()
    assert all(isinstance(context, RunContext) for context in client.contexts)
    assert client.contexts[-1].subject == "123456"
    assert client.contexts[-1].rig is None  # no aibs_comp_id here
