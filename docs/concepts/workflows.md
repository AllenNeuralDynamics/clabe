# Workflows and steps

A **workflow** is an experiment written down as an ordered list of **steps**. A step is a plain Python function —
sync or async — registered with `@wf.step`. The workflow is data: it can be listed, checked, shown and varied without
running anything.

```python
from clabe.workflow import Workflow

wf = Workflow("vr-foraging", exit_prompt_timeout=60)


@wf.step
def session(launcher: Launcher) -> Session: ...


@wf.step(title="Trainer state")
def trainer_state(session: Session) -> TrainerState: ...


@wf.step(barrier=True)
def register(launcher: Launcher, session: Session, rig: Rig) -> Path: ...
```

## Wiring by name

A step's **name** is its function's name, and what it returns is its **output**, stored under that name. A step's
**parameters** name the outputs it needs: `trainer_state(session)` receives what `session` returned. Two names are
**built-ins** available to every step: `launcher` (the launcher running the workflow) and `run` (the run itself).

That is the only way values move between steps. There is no shared dictionary and no global state: what a step does
not return, later steps cannot see. The rules are checked when the file is imported — a parameter that names no
earlier step, a duplicate name, `*args` — so a misspelt input fails immediately rather than mid-session.

Outputs are passed **by reference**. By convention a step does not mutate what it receives; it returns a new value
(`session.model_copy(update=...)`). This is what makes going back safe: an earlier output is still what it was.

## Dependencies

Wiring makes the dependencies explicit: `register` depends on `session` and `rig`, and transitively on whatever
those depend on. The run uses this graph to decide what to re-run when the user goes back (see [Runs](runs.md)), and
`clabe check` uses the annotations along it to check, statically, that each consumer accepts what its producer
returns.

## Step options

| Option | Meaning |
|---|---|
| `title` | Shown to users instead of the name. |
| `barrier=True` | Once it completes, neither it nor any earlier step can run again (it has side effects in the world: a folder created, a task started). |
| `undo=fn` | The step's side effects can be reverted: `fn(output)` is called when going back invalidates it. Exclusive with `barrier`. |
| `optional="question?"` | The run asks first; "no" skips it and its output is `None`. |
| `on_error="warn"` | A failure only warns; the output is `None` and the run continues. |
| `cancellable=True` | An async step that going back may interrupt while it is busy. |
| `codec=` | How the output is recorded (see [Events and records](records.md)). |

Reusable steps (`clabe.steps`) carry their own defaults — `register()` is a barrier — through
`step_defaults(...)`; options given when declaring a step win. A **check** is smaller still: a function that raises
when something is wrong, run with others by one `steps.preflight(...)` step that reports every failure at once. Labs
keep their own steps and checks in a package their experiments import: see
[Reusable steps and checks](../articles/reusable_steps.md).

## Composition

A workflow can be derived from another without copying it:
`wf.variant("vr-foraging-fip").insert_after("rig", fip_rig).replace("qc", my_qc)`. A file may offer several
workflows; `clabe run file.py --experiment-name ...` picks one, `clabe list` shows them.

A function decorated with `@experiment` (the API before workflows) is a workflow of a single step named `experiment`; it
runs unchanged, but gets none of the step-level benefits.

## Why steps, not a script

A script runs top to bottom and knows nothing about itself. Declaring steps lets CLABE:

- show the whole session before it runs, and where it is now;
- go back to a step and re-run only what depends on it, instead of restarting;
- retry a failed step instead of losing the session;
- lock what must not run twice (barriers);
- record each step's output with the exact inputs it used;
- check every experiment in CI without hardware.
