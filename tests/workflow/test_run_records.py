import asyncio
import functools
import json
import logging
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from clabe.ui import ConfirmRequest
from clabe.workflow import GoBack, Run, RunStatus, Workflow, codecs, export_artifact
from clabe.workflow._records import ACTIVE_STEP
from clabe.workflow.events import ArtifactExported, Notify, StepFinished, StepStarted

from ._helpers import ScriptedClient, events_of, execute


class Subject(BaseModel):
    id: str


def _lines(record_dir: Path) -> list[dict]:
    text = (record_dir / "outputs.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_every_attempt_is_one_line_with_its_lineage(tmp_path):
    calls = []
    wf = Workflow("demo")

    @wf.step
    def session() -> Subject:
        calls.append("session")
        return Subject(id=f"mouse-{calls.count('session')}")

    wf.step(lambda: "rig-1", name="rig")

    @wf.step
    def confirm(run, session):
        if not run.ask(ConfirmRequest(label="ok?")):
            raise GoBack("session")

    wf.step(lambda session, rig: None, name="register")

    run, _ = execute(wf, [False, True], record_dir=tmp_path)

    lines = _lines(tmp_path)
    assert [(line["step"], line["index"], line["attempt"]) for line in lines] == [
        ("session", 0, 1),
        ("rig", 1, 1),
        ("session", 0, 2),
        ("confirm", 2, 2),
        ("register", 3, 1),
    ]
    session_2 = lines[2]
    assert (session_2["codec"], session_2["output"], session_2["repr"]) == ("auto", {"id": "mouse-2"}, None)
    assert lines[4]["inputs"] == {"session": 2, "rig": 1}
    finished = {(e.step, e.attempt): e for e in events_of(run, StepFinished)}
    assert session_2["sha256"] == finished[("session", 2)].output_sha256
    assert [e.attempt for e in events_of(run, StepStarted) if e.step == "confirm"] == [1, 2]


def test_auto_masks_secrets_and_falls_back_to_repr(tmp_path):
    class Credentials(BaseModel):
        password: SecretStr

    wf = Workflow("demo")
    wf.step(lambda: Credentials(password="hunter2"), name="credentials")
    wf.step(lambda: object(), name="client")

    execute(wf, record_dir=tmp_path)

    credentials, client = _lines(tmp_path)
    assert "hunter2" not in json.dumps(credentials)
    assert client["lossy"] and client["output"] is None and client["repr"].startswith("<object object")


def test_native_and_custom_codecs(tmp_path):
    def shout(value: str) -> str:
        return value.upper()

    wf = Workflow("demo")
    wf.step(lambda: Subject(id="m1"), name="model", codec=codecs.model_dump)
    wf.step(lambda: {"a": 1}, name="text", codec=codecs.text)
    wf.step(lambda: "token", name="secret", codec=codecs.omit)
    wf.step(lambda: "quiet", name="custom", codec=shout)

    run, _ = execute(wf, record_dir=tmp_path)

    model, text, secret, custom = _lines(tmp_path)
    assert (model["codec"], model["output"]) == ("model_dump", {"id": "m1"})
    assert (text["codec"], text["output"]) == ("text", "{'a': 1}")
    assert (secret["codec"], secret["output"], secret["sha256"]) == ("omit", None, None)
    assert "token" not in (tmp_path / "outputs.jsonl").read_text(encoding="utf-8")
    assert custom["output"] == "QUIET" and custom["codec"].endswith("shout")
    assert events_of(run, StepFinished)[2].output_sha256 is None


def test_a_codec_can_export_artifacts(tmp_path):
    def as_file(value: bytes) -> str:
        return export_artifact(value, "frames.bin").relative_to(tmp_path / "artifacts").as_posix()

    wf = Workflow("demo")
    wf.step(lambda: b"\x00\x01", name="frames", codec=as_file)

    execute(wf, record_dir=tmp_path)

    [line] = _lines(tmp_path)
    assert line["output"] == "frames/1/frames.bin"
    assert line["artifacts"] == ["frames/1/frames.bin"]
    assert (tmp_path / "artifacts/frames/1/frames.bin").read_bytes() == b"\x00\x01"


def test_a_failing_codec_is_lossy_and_warns_but_the_step_succeeds(tmp_path):
    def broken(value):
        raise ValueError("nope")

    wf = Workflow("demo")
    wf.step(lambda: 42, name="a", codec=broken)

    run, _ = execute(wf, record_dir=tmp_path)

    [line] = _lines(tmp_path)
    assert run.status is RunStatus.COMPLETED and run.output("a") == 42
    assert (line["lossy"], line["output"], line["repr"]) == (True, None, "42")
    assert "nope" in events_of(run, Notify)[0].message


def test_export_artifact_from_sync_and_async_steps(tmp_path):
    source = tmp_path / "rig.json"
    source.write_text("{}")
    wf = Workflow("demo")

    @wf.step
    def session():
        export_artifact(Subject(id="m1"), "subject.json")
        export_artifact("notes", "notes/session.txt")
        return export_artifact(source)

    @wf.step
    async def bonsai():
        await asyncio.sleep(0)
        return export_artifact(b"\x00\x01", "frames.bin")

    run, _ = execute(wf, record_dir=tmp_path / "records")

    artifacts = tmp_path / "records" / "artifacts"
    assert json.loads((artifacts / "session/1/subject.json").read_text()) == {"id": "m1"}
    assert (artifacts / "session/1/notes/session.txt").read_text() == "notes"
    assert run.output("session") == artifacts / "session/1/rig.json"
    assert (artifacts / "bonsai/1/frames.bin").read_bytes() == b"\x00\x01"
    first, second = _lines(tmp_path / "records")
    assert first["artifacts"] == ["session/1/subject.json", "session/1/notes/session.txt", "session/1/rig.json"]
    assert second["artifacts"] == ["bonsai/1/frames.bin"]
    assert [e.path for e in events_of(run, ArtifactExported)][-1] == "bonsai/1/frames.bin"


def test_artifacts_of_a_failed_attempt_stay_on_record(tmp_path):
    attempts = []
    wf = Workflow("demo")

    @wf.step
    def flaky():
        attempts.append(1)
        export_artifact(f"attempt {len(attempts)}", "log.txt")
        if len(attempts) == 1:
            raise RuntimeError("boom")

    run, _ = execute(wf, ["retry"], record_dir=tmp_path)

    assert [e.path for e in events_of(run, ArtifactExported)] == ["flaky/1/log.txt", "flaky/2/log.txt"]
    assert (tmp_path / "artifacts/flaky/1/log.txt").read_text() == "attempt 1"
    assert [line["artifacts"] for line in _lines(tmp_path)] == [["flaky/2/log.txt"]]


def test_outputs_without_a_canonical_json_form_are_lossy_and_never_fail_the_step(tmp_path, caplog):
    wf = Workflow("demo")
    wf.step(lambda: "\udc80", name="surrogate")
    wf.step(lambda: 1, name="mixed_keys", codec=lambda value: {1: "a", "b": 2})
    wf.step(lambda: float("nan"), name="nan")

    with caplog.at_level(logging.DEBUG, logger="clabe.workflow._run"):
        run, _ = execute(wf, record_dir=tmp_path)

    assert run.status is RunStatus.COMPLETED
    text = (tmp_path / "outputs.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(line, parse_constant=pytest.fail) for line in text.splitlines()]  # strict JSON: no NaN
    assert [(line["step"], line["lossy"], line["output"]) for line in lines] == [
        ("surrogate", True, None),
        ("mixed_keys", True, None),
        ("nan", True, None),
    ]
    assert all(line["sha256"] for line in lines)
    finished = events_of(run, StepFinished)
    assert [e.output_sha256 for e in finished] == [line["sha256"] for line in lines]
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("step nan" in message for message in debug)  # the default codec's fallback is logged
    assert len(events_of(run, Notify)) == 1  # only the custom codec warns


def test_without_a_record_dir_outputs_are_not_encoded():
    calls = []

    def as_file(value):
        calls.append(value)
        return export_artifact(b"x", "x.bin")

    wf = Workflow("demo")
    wf.step(lambda: 1, name="a", codec=as_file)

    run, _ = execute(wf)

    assert run.status is RunStatus.COMPLETED and calls == []
    assert events_of(run, Notify) == []
    assert events_of(run, StepFinished)[0].output_sha256 is None


def test_codec_names_are_stable_for_callables_without_a_qualname():
    def scaled(value, factor):
        return value * factor

    assert codecs.codec_name(functools.partial(scaled, factor=2)) == "functools.partial"


def test_lineage_of_skipped_and_warn_only_steps(tmp_path):
    def broken():
        raise RuntimeError("boom")

    wf = Workflow("demo")
    wf.step(lambda: 1, name="opt", optional="Run it?")
    wf.step(broken, name="warned", on_error="warn")
    wf.step(lambda opt, warned: None, name="last")

    run, _ = execute(wf, [False], record_dir=tmp_path)

    assert [(line["step"], line["inputs"]) for line in _lines(tmp_path)] == [("last", {"opt": 0, "warned": 1})]
    assert events_of(run, StepFinished)[-1].inputs == {"opt": 0, "warned": 1}


def test_the_active_step_is_cleared_after_each_step_and_after_an_aborted_one():
    seen = []

    class Probe(Run):
        def _apply_pending(self) -> None:
            seen.append(ACTIVE_STEP.get())  # on the run thread, between steps
            super()._apply_pending()

    wf = Workflow("demo")

    @wf.step
    def a():
        return ACTIVE_STEP.get().step.name

    @wf.step
    def b(run):
        run.ask(ConfirmRequest(label="wait"))

    run = Probe(wf)
    client = ScriptedClient(run, [lambda r, _: r.abort()])
    run.start()
    assert run.wait(5)
    for thread in client.threads:
        thread.join(5)

    assert run.status is RunStatus.ABORTED and run.output("a") == "a"
    assert len(seen) == 3 and seen == [None, None, None]


def test_exporting_a_directory_copies_it(tmp_path):
    source = tmp_path / "config"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "rig.json").write_text("{}")
    wf = Workflow("demo")

    @wf.step
    def a():
        export_artifact(source)
        try:
            export_artifact(source)
        except FileExistsError as exc:
            return str(exc)

    run, _ = execute(wf, record_dir=tmp_path / "records")

    assert (tmp_path / "records/artifacts/a/1/config/nested/rig.json").read_text() == "{}"
    assert "already exported" in run.output("a")


@pytest.mark.parametrize("name", ["../escape.txt", "/abs.txt", "", "C:x", "\\\\srv\\share\\x", ".", "a\\..\\b"])
def test_export_artifact_rejects_unsafe_names(tmp_path, name):
    wf = Workflow("demo")

    @wf.step
    def a():
        try:
            export_artifact("x", name)
        except ValueError as exc:
            return str(exc)

    run, _ = execute(wf, record_dir=tmp_path)
    assert "artifact name" in run.output("a")


def test_export_artifact_rejects_duplicates_and_needs_a_name_for_content(tmp_path):
    wf = Workflow("demo")

    @wf.step
    def a():
        export_artifact("x", "same.txt")
        errors = []
        for call in (lambda: export_artifact("y", "same.txt"), lambda: export_artifact(b"z")):
            try:
                call()
            except (FileExistsError, ValueError) as exc:
                errors.append(type(exc).__name__)
        return errors

    run, _ = execute(wf, record_dir=tmp_path)
    assert run.output("a") == ["FileExistsError", "ValueError"]


def test_export_artifact_only_works_inside_a_step_of_a_recording_run():
    with pytest.raises(RuntimeError, match="inside a running step"):
        export_artifact("x", "a.txt")

    wf = Workflow("demo")

    @wf.step
    def a():
        try:
            export_artifact("x", "a.txt")
        except RuntimeError as exc:
            return str(exc)

    run, _ = execute(wf)  # no record_dir
    assert run.status is RunStatus.COMPLETED and "record directory" in run.output("a")
