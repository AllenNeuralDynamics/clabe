# Reusable steps and checks

Write a step or check once in a package that every experiment repository imports. Each experiment then declares it
in one line.

## 1. Use the built-in steps { #built-in-steps }

```python
from clabe import steps

wf.step(steps.session())
wf.step(steps.review("session", "rig", back_to="session"))
wf.step(steps.register())
```

| Step | Takes | Does | Defaults |
|---|---|---|---|
| `steps.session()` | `launcher` | Asks who is running and on which animal | |
| `steps.review(*names, back_to=)` | the named outputs | Shows them in one table; "Looks good" continues, "Go back" re-runs `back_to` (default: the first name) and what depends on it | |
| `steps.preflight(*checks)` | what the checks take | Runs every [check](#checks); fails once, listing each failure | |
| `steps.free_space(bytes)` | `rig` | Fails when the data drive is short of space | |
| `steps.register()` | `launcher`, `session`, `rig` | Creates the session's data directory | barrier |
| `steps.waterlog(earned_water)` | `launcher`, `session` | Logs the water earned | `on_error="warn"` |

Options passed to `wf.step(...)` override the defaults.

## 2. Write a reusable step

An outer function takes the settings. The inner function is the step, and
[`step_defaults`][clabe.workflow.step_defaults] sets its default options:

```python
# my_lab/steps.py
from clabe.workflow import step_defaults


def calibrate_valves(*, tolerance: float = 0.05):
    """A `calibrate_valves` step: measures every valve; returns the calibration."""

    @step_defaults(title="Calibrate valves", cancellable=True)
    async def calibrate_valves(rig: MyRig, launcher: Launcher) -> ValveCalibration:
        return await measure(rig.valves, tolerance=tolerance)

    return calibrate_valves
```

```python
# an experiment repository
from my_lab import steps as lab_steps

wf.step(lab_steps.calibrate_valves(tolerance=0.02))
wf.step(lab_steps.calibrate_valves(), name="recalibrate", title="Calibrate again")
```

- The inner function's name is the step's name.
- Use the usual input names (`session`, `rig`, `launcher`), annotated.
- To fail, raise an exception whose message tells the user what to do.
- Test it as a plain function: `calibrate_valves()(rig=fake_rig, launcher=MagicMock())`.
- For the available options, see [step options](workflows.md#step-options).

## 3. Write a check { #checks }

A check returns nothing and raises an exception when its condition is not met. It takes its inputs by name, as a
step does.

| Check | Takes | Fails when |
|---|---|---|
| `checks.free_space(bytes, directory=)` | `rig` (or nothing, given a path) | The data folder's drive has less free space |
| `checks.exists(path)` | nothing (or `rig`, given a function) | The file, folder or share cannot be reached |
| `checks.that(predicate, message)` | the predicate's parameters | The predicate returns something false |

```python
# my_lab/checks.py
from clabe import checks


def bonsai_installed(rig: MyRig) -> None:
    if not (rig.bonsai_directory / "Bonsai.exe").exists():
        raise FileNotFoundError(f"No Bonsai in {rig.bonsai_directory}: run ./scripts/deploy.ps1 on this rig.")


def camera_count(expected: int):
    return checks.that(lambda rig: len(rig.cameras) == expected, f"The rig needs {expected} cameras.")


RIG_READY = [checks.free_space(200e9), checks.exists(r"\\allen\aind\scratch"), bonsai_installed]
```

## 4. Run checks as one step

```python
from my_lab import checks as lab_checks

wf.step(steps.preflight(*lab_checks.RIG_READY, lab_checks.camera_count(2)))
```

```text
3 checks failed:
- Only 42.0 GB free on D:\data; 200.0 GB needed.
- \\allen\aind\scratch does not exist or cannot be reached.
- No Bonsai in C:\bonsai: run ./scripts/deploy.ps1 on this rig.
```

- Every check runs, and Retry runs them all again.
- If a workflow has several `preflight` steps, give each one a distinct `name=`.
- Outside a workflow: `checks.run(*lab_checks.RIG_READY, rig=rig)` raises
  [`ChecksFailed`][clabe.checks.ChecksFailed].

## From the resource monitor

`clabe.resource_monitor` is deprecated. Replace it as follows:

| Before | Now |
|---|---|
| `available_storage_constraint_factory_from_rig(rig, n)` | `checks.free_space(n)` |
| `available_storage_constraint_factory(drive, n)` | `checks.free_space(n, directory=drive)` |
| `remote_dir_exists_constraint_factory(path)` | `checks.exists(path)` |
| `Constraint(name, predicate, fail_msg_handler=...)` | `checks.that(predicate, message)`, or a function that raises |
| `ResourceMonitor([...]).run()` | `wf.step(steps.preflight(...))`, or `checks.run(..., rig=rig)` |

Unlike the resource monitor, `steps.preflight` and `checks.run` run every check. They also measure free space where
the data goes, instead of at the drive root.
