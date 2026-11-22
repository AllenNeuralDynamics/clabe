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
| `launcher.log`, `repository-state.json` | The diagnostic log; the repository and submodules |

`events.jsonl` includes `command_finished` (each app command, with hashes) and `record_resolved` (each store record
used, copied under `artifacts/<step>/<attempt>/stores/`).

## Replaying a session

```bash
clabe replay <session>/Behavior/Logs/.launcher/events.jsonl experiments/vr_foraging.py --allow-dirty
```

- Reruns headless with the recorded answers; stops at a prompt that differs. Secrets can't be replayed.
- In a test: `LocalClient(run, ReplayClient("events.jsonl")).drive()`.

## Choosing how an output is recorded

```python
from clabe.workflow import codecs


@wf.step(codec=codecs.omit)  # credentials, huge objects: lineage only
def credentials() -> Credentials: ...


@wf.step(codec=codecs.text)  # repr(), truncated
def handle() -> SerialPort: ...


@wf.step(codec=lambda frames: {"n": len(frames), "first": frames[0].timestamp})  # your own
def frames() -> list[Frame]: ...
```

- Default `codecs.auto`: a pydantic model as its JSON, anything else via pydantic's serialization.
- A failing codec falls back to `repr` (line marked `lossy`); it never fails the step.

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

- Only inside a running step (and its tasks or `asyncio.to_thread`; not a new `threading.Thread`).
- For provenance-sized files, not session data.

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

- Written at start, rewritten with the outcome at the end.
- Secrets stay out: URL credentials stripped; `TOKEN`/`KEY`/`SECRET`/`PASSWORD`/`AUTH`-like values masked; only
  `CLABE_*` and `OTEL_*` environment variables kept.
