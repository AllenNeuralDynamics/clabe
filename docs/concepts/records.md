# Events and records

A run is recorded as it happens, without the experiment asking for it. The records answer three questions about a
session: **what was decided** (events), **what was computed from what** (outputs and artifacts), and **with what
code, environment and machine** (the manifest).

## Events

Every change in a run is an **event**, appended to the run's event log with a sequence number: the run started and
finished; a step started, finished, failed or was skipped; steps were invalidated by going back; a prompt opened and
closed (with the answer and who gave it); a message, a header, an activity; a branch started or ended; an artifact
was exported.

The event log is the run's source of truth for clients. The run's API serves a **snapshot** (the current state, with
the sequence number it reflects) and a **stream** of events after any sequence number, so a client that connects
late, or reconnects, catches up exactly. The same events are written to `events.jsonl`, one JSON object per line.

## Outputs and lineage

Every attempt of every step appends one line to `outputs.jsonl`: the step, its position, the attempt, the attempts of
the inputs it consumed (its exact lineage: `register` used `session` attempt 2 and `rig` attempt 1), the recorded
output with its SHA-256, and the artifacts it exported. A **codec** decides how an output is recorded: by default a
pydantic model's own JSON, otherwise pydantic's generic serialization; `codec=codecs.omit` records no value (for
credentials or huge objects) while keeping the lineage.

Outputs are kept apart from events on purpose: events are streamed to every client, outputs can be large or
sensitive.

## Commands and configuration

Two kinds of events pin down what a step used:

- every command an app runs (Bonsai, Open Ephys, a script) ends with a `command_finished` event: the command line
  (secret flags masked), the program and its hash, the hash of every file it named (the workflow, its inputs, the
  `Bonsai.config` next to Bonsai), the machine, the exit code and the duration;
- every record a step takes from a store (a rig, a trainer state) is a `record_resolved` event — which record, from
  which store, its content hash and revision when the store knows it — and the record itself is saved as an artifact
  of the step, so the session keeps exactly what it used even if the store changes later.

## Artifacts

`export_artifact(...)`, called inside a step, saves a file, bytes, text or a model under
`artifacts/<step>/<attempt>/` and lists it with the attempt's output — for provenance-sized files such as the
configuration sent to an app, a QC figure or a report, not bulk data.

## The manifest

`run.json` is written when the run starts and completed when it ends:

| Section | What it holds |
|---|---|
| code | CLABE's version, the command line, the repository and its submodules (commits, tags, branch, dirty flag), the diff of uncommitted changes (in full up to 5 MB, else its summary) and the untracked files, the workflow's steps and the hash of every file defining one |
| environment | Python's version and executable, `uv`'s version, a copy of `uv.lock` and its hash, the installed distributions |
| machine | host name, OS, CPU, memory, free disk, time zone, user, GPUs |
| config | the launcher's settings, the clabe.yml files in effect (with hashes), the `CLABE_*` and `OTEL_*` environment variables |
| workers | each other machine the run used: host, clabe version, its checkout as verified, and every command it ran there (with step and attempt) |
| outcome | status, reason, start and end, exit code |

## Telemetry

When OpenTelemetry is configured, the run is a trace: a root span for the run and one span per step attempt, with
apps and remote jobs nested under them, even on other machines.

## Where it ends up

During the run the records are written to the launcher's temporary folder; when the run ends they are copied with the
logs into the session folder (`<session>/Behavior/Logs/.launcher/`): `run.json`, `events.jsonl`, `outputs.jsonl`,
`artifacts/`, `uv.lock`, `repository.diff` (when dirty), `launcher.log`, `repository-state.json`.

## Reproducibility

Together these let you reconstruct a session: which commit and environment ran it, every decision a person made, the
configuration it took from stores, the programs it ran with their files, and the inputs and outputs of each step.
**Replaying** goes one step further: `clabe replay events.jsonl experiment.py` runs the experiment again, answering
every prompt with the recorded answer (and failing clearly if the workflow now asks something else) — to reproduce a
session's decisions, or to test a changed workflow against a real session. What the records do not capture is the
data the apps recorded themselves, and decisions made outside prompts (clicking a step to go back, aborting).
