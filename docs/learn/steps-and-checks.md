# Steps and checks

clabe ships steps most experiments need, and you can write your own once, in a package every experiment imports.

## Built-in steps { #built-in-steps }

```python
from clabe import steps

wf.step(steps.session())
wf.step(steps.review("session", "rig", back_to="session"))
wf.step(steps.register())
```

| Step | Does |
|---|---|
| `steps.session()` | Asks who is running, on which animal |
| `steps.review(*names, back_to=)` | Shows those outputs in one table; **Go back** re-runs `back_to` |
| `steps.preflight(*checks)` | Runs every [check](#checks); fails once, listing each failure |
| `steps.free_space(bytes)` | Fails when the data drive is short of space |
| `steps.register()` | Creates the session's data folder (a barrier) |
| `steps.curriculum(before=, upload=)` | Runs the curriculum and saves its suggestion |
| `steps.remote(computer_name)` | Connects to another rig PC ([Two PCs](../rigs/two-pcs.md)) |
| `steps.waterlog(earned_water)` | Logs the water earned (only warns on failure) |

Options passed to `wf.step(...)` override a built-in's defaults: `wf.step(steps.register(), title="Create folder")`.

## Write a reusable step

An outer function takes the settings; the inner function is the step.
[`step_defaults`][clabe.workflow.step_defaults] gives it default options:

```python
# my_lab/steps.py
from clabe.workflow import step_defaults


def calibrate_valves(*, tolerance: float = 0.05):
    @step_defaults(title="Calibrate valves", cancellable=True)
    async def calibrate_valves(rig: MyRig) -> ValveCalibration:
        return await measure(rig.valves, tolerance=tolerance)

    return calibrate_valves
```

```python
# an experiment
wf.step(lab_steps.calibrate_valves(tolerance=0.02))
wf.step(lab_steps.calibrate_valves(), name="recalibrate", title="Calibrate again")
```

- The inner function's name is the step's name; use the usual input names (`session`, `rig`, `launcher`).
- Fail with an exception whose message tells the user what to do.
- Test it as a function: `calibrate_valves()(rig=fake_rig)`.

## Checks { #checks }

A check returns nothing and raises when its condition is not met. It takes inputs by name, like a step.

```python
from clabe import checks


def bonsai_installed(rig: MyRig) -> None:
    if not (rig.bonsai_directory / "Bonsai.exe").exists():
        raise FileNotFoundError(f"No Bonsai in {rig.bonsai_directory}: run ./scripts/deploy.ps1.")


RIG_READY = [checks.free_space(200e9), checks.exists(r"\\allen\aind\scratch"), bonsai_installed]
wf.step(steps.preflight(*RIG_READY, checks.that(lambda rig: len(rig.cameras) == 2, "Two cameras needed.")))
```

```text
3 checks failed:
- Only 42.0 GB free on D:\data; 200.0 GB needed.
- \\allen\aind\scratch does not exist or cannot be reached.
- No Bonsai in C:\bonsai: run ./scripts/deploy.ps1.
```

| Check | Fails when |
|---|---|
| `checks.free_space(bytes, directory=)` | The data folder's drive has less free space |
| `checks.exists(path)` | The file, folder or share cannot be reached |
| `checks.that(predicate, message)` | The predicate returns something false |

Every check runs, and Retry runs them all again. Outside a workflow, `checks.run(*RIG_READY, rig=rig)` raises
[`ChecksFailed`][clabe.checks.ChecksFailed].
