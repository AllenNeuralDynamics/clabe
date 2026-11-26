"""Replaying a recorded session: the same answers, in order, to the same questions."""

import pytest

from clabe import ui
from clabe.workflow import GoBack, LocalClient, ReplayClient, ReplayMismatch, Run, RunStatus, Workflow
from tests.conftest import MockClient


def _workflow(label: str = "Subject") -> Workflow:
    attempts = {"calibrate": 0}
    wf = Workflow("demo")

    @wf.step
    def subject() -> str:
        return ui.text(label)

    @wf.step
    def gain(subject: str) -> float:
        return ui.number("Gain", minimum=0, maximum=1)

    @wf.step
    def review(subject: str, gain: float) -> None:
        if not ui.confirm(f"Use {subject} at {gain}?"):
            raise GoBack("subject")

    @wf.step
    def calibrate(review: None) -> str:
        attempts["calibrate"] += 1
        if attempts["calibrate"] == 1:
            raise RuntimeError("valve did not respond")
        return "ok"

    return wf


def _record(tmp_path) -> Run:
    answers = {"text": ["mouse-1", "mouse-2"], "number": [0.5, 0.25], "confirm": [False, True], "pick": ["retry"]}
    run = Run(_workflow(), record_dir=tmp_path)
    LocalClient(run, MockClient(answers)).drive()
    assert run.status is RunStatus.COMPLETED
    return run


def test_a_replayed_session_reaches_the_same_outputs(tmp_path):
    recorded = _record(tmp_path)
    client = ReplayClient(tmp_path / "events.jsonl")
    replay = Run(_workflow())
    LocalClient(replay, client).drive()
    assert replay.status is RunStatus.COMPLETED
    for step in ("subject", "gain", "calibrate"):
        assert replay.output(step) == recorded.output(step)
    assert (client.replayed, client.remaining) == (7, 0)


def test_a_changed_workflow_is_a_mismatch(tmp_path):
    _record(tmp_path)
    replay = Run(_workflow(label="Animal"))
    with pytest.raises(ReplayMismatch, match="'Animal'.*'Subject'"):
        LocalClient(replay, ReplayClient(tmp_path / "events.jsonl")).drive()
    assert replay.status is RunStatus.ABORTED
