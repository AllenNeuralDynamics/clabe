# Workflows

An experiment is a **workflow**: an ordered list of steps. Each step is a plain function; its parameters name the
earlier steps whose outputs it needs, and what it returns is stored under its own name. The launcher runs the steps
in order, shows them to every client (the TUI, the console, the web UI) and lets the user go back, retry or skip.

```python
from clabe import steps, ui
from clabe.launcher import Launcher
from clabe.workflow import GoBack, Workflow

wf = Workflow("vr-foraging", exit_prompt_timeout=60)

wf.step(steps.session())                       # a `session` step: who is running, on which animal


@wf.step(title="Trainer state")
def trainer_state(session: Session) -> TrainerState:
    return store_for(session).resolve(TRAINER_STATE)


@wf.step
def rig(session: Session) -> AindVrForagingRig: ...


wf.step(steps.review("session", "trainer_state", back_to="session"))
wf.step(steps.free_space(200e9))
wf.step(steps.register())                      # a barrier: nothing before it runs again


@wf.step(title="Run Bonsai", barrier=True)
async def bonsai(launcher: Launcher, session: Session, rig: AindVrForagingRig) -> None: ...


@wf.step(optional="Generate a QC report?")
def qc(launcher: Launcher) -> QcReport: ...
```

Run it with `clabe run my_experiment.py` (it is found by name; pass `--experiment-name` when a file declares
several) or `Launcher(settings=...).run_experiment(wf)`. A legacy `@experiment` function is a workflow of one step.

## Wiring and sharing values

- A step's parameters are resolved **by name**: from earlier steps' outputs and the built-ins `launcher` and `run`.
  Unknown or forward references, duplicate names and `*args` fail when the file is imported.
- Outputs are passed **by reference**. By convention a step never mutates what it receives; it returns a new value
  (`session.model_copy(update=...)`). Several values: return a model or a `NamedTuple`, or split the step.
- Every output is recorded in the run's `outputs.jsonl` (see [Logging](logging.md)); `codec=` chooses how.

## Going back, barriers and failures

- **Going back** to a step (clicking it in the web UI, `raise GoBack("session")` from a step, `run.go_back(...)`)
  runs it again along with every step that depends on it, directly or not. Independent steps keep their outputs.
- A **barrier** step (`barrier=True`) locks itself and everything before it once it has run — e.g. once the session
  folder exists or the task has started. A step with reversible side effects declares `undo=` instead.
- A **failing** step pauses the run with *Retry / Go back / Abort*. `on_error="warn"` only warns and continues with
  `None`.
- An **optional** step (`optional="question?"`) asks first; a skipped step produces `None`, so its consumers must
  accept `None`.
- `cancellable=True` lets going back interrupt a busy async step; abort always interrupts.

## Shared steps

`clabe.steps` builds common steps. Each returns a plain function wired by name (`session`, `rig`, `launcher`) and
carrying default options, which the options you pass override:

| Step | Takes | Does | Defaults |
|---|---|---|---|
| `steps.session()` | `launcher` | asks who is running and on which animal | |
| `steps.review(*names, back_to=)` | the named outputs | shows them; "Go back" goes back to `back_to` | |
| `steps.free_space(bytes)` | `rig` | fails when the data drive is short of space | |
| `steps.register()` | `launcher`, `session`, `rig` | creates the session's data directory | barrier |
| `steps.transfer(service)` | `launcher`, `session` | hands the data to a data-transfer service | barrier |
| `steps.waterlog(earned_water)` | `launcher`, `session` | logs the water earned | warns on failure |

Your own reusable steps can carry defaults the same way, with `clabe.workflow.step_defaults(barrier=True, ...)`.

## Variants

`wf.variant("vr-foraging-fip").insert_after("rig", fip_rig).replace("qc", my_qc)` derives a workflow without
copying it.

## Checking workflows in CI

`clabe check path/to/experiments --strict` imports the files (so every wiring rule is enforced) and compares each
parameter's annotation with the annotation of the step that produces it: subclasses fit, `int` fits `float`, an
optional or `warn` producer yields `T | None`. With `--strict`, unannotated wiring is an error too. Nothing runs.

See [examples/behavior_launcher.py](https://github.com/AllenNeuralDynamics/clabe/blob/main/examples/behavior_launcher.py)
for a complete workflow.
