"""Records resolved from a store inside a run are on record: which, from where, and the record itself."""

import hashlib
import json

from pydantic import BaseModel

from clabe.stores import Kind, LocalFileStore
from clabe.workflow import Run, RunStatus, Workflow
from clabe.workflow.events import RecordResolved


class Widget(BaseModel):
    """A custom record."""

    value: int


def test_a_record_resolved_by_a_step_is_recorded_and_kept(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "widget.json").write_text(json.dumps({"value": 42}))
    store = LocalFileStore(library)
    wf = Workflow("demo")
    wf.step(lambda: store.resolve(Kind(Widget)).value, name="widget")
    run = Run(wf, record_dir=tmp_path / "records")
    run.start()
    assert run.wait(10) and run.status is RunStatus.COMPLETED and run.output("widget") == 42

    (event,) = [e for e in run.events.since(0) if isinstance(e, RecordResolved)]
    assert (event.step, event.kind, event.label) == ("widget", "widget", str(library / "widget.json"))
    kept = tmp_path / "records" / event.artifact
    assert json.loads(kept.read_text()) == {"value": 42}
    assert event.sha256 == hashlib.sha256(kept.read_bytes()).hexdigest()


def test_outside_a_run_nothing_is_recorded(tmp_path, mock_frontend):
    from tests.conftest import use_client

    use_client(mock_frontend)
    (tmp_path / "widget.json").write_text(json.dumps({"value": 1}))
    assert LocalFileStore(tmp_path).resolve(Kind(Widget)) == Widget(value=1)
