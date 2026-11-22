# Reusable steps and checks

Most of an experiment is shared: asking who runs on which animal, checking the rig is ready, creating the session
folder, handing the data over. Write those once, in a module others can import, and every experiment repository
declares them in one line. clabe ships two such modules, [`clabe.steps`](#steps) and [`clabe.checks`](#checks), built
exactly as you would build your own.

## Steps

A step is a plain function: its parameters name what it needs (other steps' outputs, `launcher`, `run`) and what it
returns is stored under its name. A **reusable step** is a function that *builds* one — so it can take settings — and
gives it sensible defaults with `step_defaults`:

```python
# my_lab/steps.py — in your package, next to your rigs and tasks
from clabe.workflow import step_defaults


def calibrate_valves(*, tolerance: float = 0.05):
    """A `calibrate_valves` step: measures every valve of the rig; returns the calibration."""

    @step_defaults(title="Calibrate valves", cancellable=True)
    async def calibrate_valves(rig: MyRig, launcher: Launcher) -> ValveCalibration:
        return await measure(rig.valves, tolerance=tolerance)

    return calibrate_valves
```

```python
# an experiment repository
from clabe import steps
from my_lab import steps as lab_steps

wf.step(steps.session())
...
wf.step(lab_steps.calibrate_valves(tolerance=0.02))
wf.step(lab_steps.calibrate_valves(), name="recalibrate", title="Calibrate again")  # declared options win
```

Conventions that keep them easy to reuse:

- **The inner function's name is the step's name**, so later steps receive its output under it. Pick a name worth
  sharing (`rig`, `session`, `calibration`); callers rename with `name=`.
- **Take inputs by the names experiments already use** — `session`, `rig`, `launcher` — and annotate them, so
  `clabe check --strict` can verify the wiring. Take a protocol or a base class (anything with a `data_directory`)
  rather than one rig's model when the step does not care.
- **Settings go to the outer function**, inputs to the inner one. When a value may come from the rig or be fixed,
  accept a function of the rig (`directory=lambda rig: rig.data_directory`), as `steps.register` does.
- **Defaults belong to the step**: a barrier for what must not run twice, `on_error="warn"` for best-effort work,
  `cancellable=True` for long async work. See [Workflows](workflows.md#going-back-barriers-and-failures).
- **Fail by raising** with a message the person at the rig can act on; clabe offers Retry / Go back / Abort.
- **Test them as functions**: `calibrate_valves()(rig=fake_rig, launcher=MagicMock())` — no workflow needed.

## Checks

A **check** is the smallest reusable piece: a function that returns nothing and **raises when something is wrong**,
taking what it needs by name like a step. [`clabe.checks`][clabe.checks] has the common ones:

| Check | Takes | Fails when |
|---|---|---|
| `checks.free_space(bytes, directory=)` | `rig` (or nothing, given a path) | the data drive has less free space |
| `checks.exists(path)` | nothing (or `rig`, given a function) | the file, folder or share cannot be reached |
| `checks.that(predicate, message)` | the predicate's parameters | the predicate returns something false |

and your own are plain functions:

```python
# my_lab/checks.py
from clabe import checks


def bonsai_installed(rig: MyRig) -> None:
    """Fails when the rig's Bonsai environment has not been set up."""
    if not (rig.bonsai_directory / "Bonsai.exe").exists():
        raise FileNotFoundError(f"No Bonsai in {rig.bonsai_directory}: run ./scripts/deploy.ps1 on this rig.")


def camera_count(expected: int):
    """Fails when the rig does not have ``expected`` cameras."""
    return checks.that(lambda rig: len(rig.cameras) == expected, f"The rig needs {expected} cameras.")


RIG_READY = [checks.free_space(200e9), checks.exists(r"\\allen\aind\scratch"), bonsai_installed]
```

`steps.preflight` runs them as one step. **Every check runs**, so the person at the rig sees everything that is wrong
at once, and Retry runs them all again:

```python
from clabe import steps
from my_lab import checks as lab_checks

wf.step(steps.preflight(*lab_checks.RIG_READY, lab_checks.camera_count(2)))
wf.step(steps.preflight(lambda session: ..., name="session_ok", title="Check the session"))  # several are fine
```

```text
3 checks failed:
- Only 42.0 GB free on D:\data; 200.0 GB needed.
- \\allen\aind\scratch does not exist or cannot be reached.
- No Bonsai in C:\bonsai: run ./scripts/deploy.ps1 on this rig.
```

The step takes the union of what its checks take (here `rig`); parameters with a default keep it. A check may
`raise GoBack(...)` or `Abort(...)` like any step. Outside a workflow — a script, a test, a legacy `@experiment` —
run them directly:

```python
checks.run(*lab_checks.RIG_READY, rig=rig)  # raises checks.ChecksFailed listing every failure
```

`steps.free_space(bytes)` is `steps.preflight(checks.free_space(bytes))` named `free_space`.

## Sharing them

Keep a lab's steps and checks in a package every experiment repository depends on (e.g. `my_lab.steps`,
`my_lab.checks`), versioned like the rest of its code — the run records the version used. Export them in `__all__`
and give each a docstring: they appear in that package's API reference and in `clabe check`'s output. Generic ones
that every lab could use belong in clabe itself.

## From the resource monitor

`clabe.resource_monitor` (`ResourceMonitor`, `Constraint` and its factories) still works, now built on these checks,
but warns that it is deprecated:

| Before | Now |
|---|---|
| `available_storage_constraint_factory_from_rig(rig, n)` | `checks.free_space(n)` |
| `available_storage_constraint_factory(drive, n)` | `checks.free_space(n, directory=drive)` |
| `remote_dir_exists_constraint_factory(path)` | `checks.exists(path)` |
| `Constraint(name, predicate, fail_msg_handler=...)` | `checks.that(predicate, message)`, or a function that raises |
| `ResourceMonitor([...]).run()` | `wf.step(steps.preflight(...))`, or `checks.run(..., rig=rig)` |

Two differences: every check runs (the monitor stopped at the first failure), and free space is measured where the
data goes — the nearest existing folder of the data directory, not its drive's root — which is what matters for
mounted folders and network shares.
