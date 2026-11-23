# Run records

What a run writes, and how to control and read it. For the ideas, see [Records](../concepts/index.md#records).

## The files

In the session folder, under `Behavior/Logs/.launcher/`:

| File | Contents |
|---|---|
| `run.json` | The [manifest](#the-manifest-runjson): code, environment, machine, configuration, outcome |
| `events.jsonl` | Every event, one JSON object per line |
| `outputs.jsonl` | One line per step attempt: output, lineage, artifacts |
| `artifacts/<step>/<attempt>/` | Files steps exported |
| `repository.diff` | Uncommitted changes, when the repository was dirty |
| `uv.lock` | The lockfile the run used |
| `launcher.log`, `repository-state.json` | The diagnostic log, and the state of the repository and its submodules |

`events.jsonl` includes `command_finished` (each app command, with hashes, and its `started_at` / `ended_at` in UTC)
and `record_resolved` (each store record used, copied under `artifacts/<step>/<attempt>/stores/`). A step's
`CommandResult` output carries the same `started_at` / `ended_at`.

## Replaying a session

```bash
clabe replay <session>/Behavior/Logs/.launcher/events.jsonl experiments/vr_foraging.py --allow-dirty
```

- Reruns the experiment headless with the recorded answers. It stops at the first prompt that differs from the
  recording. Secrets cannot be replayed.
- In a test: `LocalClient(run, ReplayClient("events.jsonl")).drive()`.

## Choosing how an output is recorded

```python
from clabe.workflow import codecs


@wf.step(codec=codecs.omit)  # credentials, huge objects: lineage only
def credentials() -> Credentials: ...


@wf.step(codec=codecs.text)  # repr(), truncated
def handle() -> SerialPort: ...


@wf.step(codec=lambda frames: {"n": len(frames), "first": frames[0].timestamp})  # a custom codec
def frames() -> list[Frame]: ...
```

- The default, `codecs.auto`, records a pydantic model as its JSON and any other value through pydantic's
  serialization.
- If a codec raises, the output is recorded with `repr` and the line is marked `lossy`. The step does not fail.

## Exporting artifacts

```python
from clabe.workflow import export_artifact


@wf.step
def configure(rig: Rig, session: Session) -> Path:
    path = write_bonsai_config(rig, session)
    export_artifact(path)  # a copy, under artifacts/configure/<attempt>/
    export_artifact(rig, "rig.json")  # a model, as JSON
    export_artifact(b"...", "figures/qc.png")  # bytes, under a sub-folder
    return path
```

- Works only inside a running step, including its tasks and `asyncio.to_thread` calls. It does not work from a new
  `threading.Thread`.
- Use it for small provenance files. Do not use it for session data.

## Reading the records

```python
import json
from pathlib import Path

from clabe.workflow.events import EventLog, PromptClosed

logs = Path(session_folder) / "Behavior/Logs/.launcher"
for e in EventLog.read(logs / "events.jsonl"):
    if isinstance(e, PromptClosed):
        print(e.answered_by, e.answer)

outputs = [json.loads(line) for line in (logs / "outputs.jsonl").read_text().splitlines()]
register = next(o for o in outputs if o["step"] == "register")
print(register["inputs"])  # {"session": 2, "rig": 1}: the attempts it used
```

## The manifest: `run.json`

- Written when the run starts, and rewritten with the outcome when it ends.
- Secrets are kept out of it. Credentials in URLs are stripped. Values that look like a `TOKEN`, `KEY`, `SECRET`,
  `PASSWORD` or `AUTH` are masked. Only the `CLABE_*` and `OTEL_*` environment variables are kept.
