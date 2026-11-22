# Run records

What a run writes and how to control it. For why, see [Events and records](../concepts/records.md).

## The files

At the end of a run they are in the session folder, under `Behavior/Logs/.launcher/`:

| File | Contents |
|---|---|
| `run.json` | The [manifest](#the-manifest-runjson): code, environment, machine, configuration, outcome. |
| `events.jsonl` | Every event, one JSON object per line. |
| `outputs.jsonl` | One line per step attempt: its output, lineage and artifacts. |
| `artifacts/<step>/<attempt>/` | Files steps exported. |
| `repository.diff` | Uncommitted changes, when the repository was dirty. |
| `uv.lock` | The lockfile the run used. |
| `launcher.log`, `repository-state.json` | The diagnostic log; the repository and submodules. |

`events.jsonl` also holds `command_finished` (each command an app ran, with its program's and files' hashes) and
`record_resolved` (each store record a step used; the record is kept under `artifacts/<step>/<attempt>/stores/`).

## Replaying a session

```bash
clabe replay <session>/Behavior/Logs/.launcher/events.jsonl experiments/vr_foraging.py --allow-dirty
```

The experiment runs again (headless), each prompt answered with the recorded answer in order. A prompt that differs
from the recorded one (another kind or label) stops the replay with both; a secret answer cannot be replayed (it is
recorded masked). In code: `LocalClient(run, ReplayClient("events.jsonl")).drive()` — handy as a test that a workflow
still accepts a real session.

## Choosing how an output is recorded

By default (`codecs.auto`) a pydantic model is recorded as its own JSON and anything else through pydantic's generic
serialization. Choose another with `codec=`:

```python
from clabe.workflow import codecs


@wf.step(codec=codecs.omit)          # credentials, huge objects: lineage only
def credentials() -> Credentials: ...


@wf.step(codec=codecs.text)          # repr(), truncated
def handle() -> SerialPort: ...


@wf.step(codec=lambda frames: {"n": len(frames), "first": frames[0].timestamp})   # your own
def frames() -> list[Frame]: ...
```

A codec that fails falls back to `repr` (the line is marked `lossy`); it never fails the step.

## Exporting artifacts

```python
from clabe.workflow import export_artifact


@wf.step
def configure(rig: Rig, session: Session) -> Path:
    path = write_bonsai_config(rig, session)
    export_artifact(path)                          # a copy, under artifacts/configure/<attempt>/
    export_artifact(rig, "rig.json")               # a model, as JSON
    export_artifact(b"...", "figures/qc.png")      # bytes, under a sub-folder
    return path
```

`export_artifact` only works inside a running step (or code it calls, including its tasks and
`asyncio.to_thread`). Use it for provenance-sized files, not for the session's data.

## Reading the records

```python
import json
from pathlib import Path

from clabe.workflow.events import EventLog, PromptClosed

logs = Path(session_folder) / "Behavior/Logs/.launcher"
answers = [e for e in EventLog.read(logs / "events.jsonl") if isinstance(e, PromptClosed)]
for e in answers:
    print(e.answered_by, e.answer)

outputs = [json.loads(line) for line in (logs / "outputs.jsonl").read_text().splitlines()]
register = next(o for o in outputs if o["step"] == "register")
print(register["inputs"])        # {"session": 2, "rig": 1}: the attempts it used
```

## The manifest: `run.json`

`run.json` is written as soon as the run starts (so a crashed session still has one) and rewritten with the outcome
when it ends. Secrets are kept out: credentials in `https://user:password@...` repository URLs are stripped; the
values of secret-looking command-line flags, settings and environment variables (names containing `TOKEN`, `KEY`,
`SECRET`, `PASSWORD`, `AUTH`, ...) are masked; and only `CLABE_*` and `OTEL_*` environment variables are recorded.
