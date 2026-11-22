# Write a workflow

Build an experiment as a [`Workflow`][clabe.workflow.Workflow]: an ordered list of steps. clabe runs the steps,
shows them in every client and lets the user go back, retry or skip a step.

## 1. Declare the workflow

```python
# my_experiment.py
from clabe import checks, steps
from clabe.launcher import Launcher
from clabe.workflow import Workflow

wf = Workflow("vr-foraging", exit_prompt_timeout=60)
wf.step(steps.session())  # a `session` step: who is running, on which animal
```

- `exit_prompt_timeout`: with `None` the run waits for a client to close the session, with `0` it exits at once, and
  with `N` it counts down `N` seconds.

## 2. Add your own steps

```python
from aind_behavior_services.session import Session


@wf.step
def rig(session: Session) -> MyRig:
    return load_rig(session)


@wf.step(title="Trainer state")
def trainer_state(session: Session, rig: MyRig) -> TrainerState:
    return store_for(session).resolve(TRAINER_STATE)
```

Wiring is by name:

- Parameters name earlier steps' outputs, or `launcher` / `run`.
- The return value is stored under the step's name.
- Bad wiring (unknown names, duplicates, `*args`) fails at import.
- Return new values (`session.model_copy(update=...)`) instead of mutating inputs.

## 3. Review, check and register

```python
SHARE = r"\\allen\aind\scratch"

wf.step(steps.review("session", "trainer_state", back_to="session"))
wf.step(steps.preflight(checks.free_space(200e9), checks.exists(SHARE)))
wf.step(steps.register())  # creates the session folder; a barrier
```

See the [built-in steps](reusable_steps.md#built-in-steps) and [checks](reusable_steps.md#checks).

## 4. Run the task and optional steps { #step-options }

```python
@wf.step(title="Run Bonsai", barrier=True)
async def bonsai(launcher: Launcher, session: Session, rig: MyRig) -> None: ...


@wf.step(optional="Generate a QC report?")
def qc(launcher: Launcher) -> QcReport: ...
```

| Option | Effect |
|---|---|
| `title=` | Label shown in the clients |
| `barrier=True` | Once run, neither it nor any step before it can run again |
| `undo=` | Called with the output when going back invalidates the step (reversible side effects) |
| `optional="question?"` | Asks first; a skipped step outputs `None` |
| `when=lambda mode: mode == 1` | Runs only when the condition on earlier outputs holds; otherwise skipped, output `None` |
| `on_error="warn"` | Warns and continues with `None` instead of pausing with Retry / Go back / Abort |
| `cancellable=True` | Going back may interrupt the busy async step (abort always does) |
| `codec=` | How the output is recorded (see [Run records](records.md#choosing-how-an-output-is-recorded)) |
| `name=` | Store the output under another name |

Going back (from the web UI, or with `raise GoBack("session")`) reruns that step and every step that depends on it.

## 5. Run a step only in some cases

```python
@wf.step
def mode() -> str:
    return ui.pick("Calibration", ["measure", "reuse"], allow_none=False)


@wf.step(when=lambda mode: mode == "measure")
def calibrate(rig: MyRig) -> Calibration: ...


@wf.step(when=lambda mode: mode == "reuse")
def last_calibration(rig: MyRig) -> Calibration: ...


@wf.step
def acquire(calibrate: Calibration | None, last_calibration: Calibration | None) -> None:
    calibration = calibrate or last_calibration
```

- `when` takes earlier outputs by name, as a step does. It must be a regular function, not an async one.
- Skipped steps output `None`, so annotate the parameters that receive them as `T | None`. `clabe check` reports
  the ones that are not.
- Going back to `mode` evaluates the conditions again.
- If a step also has `optional=`, the question is asked only when the condition holds.
- If the two cases differ only in code, one step with an `if` is simpler.

## 6. Run several branches at once

```python
from clabe.workflow import concurrently


@wf.step(title="Acquire", cancellable=True)
async def acquire(rig: MyRig) -> dict[str, Any]:
    async def behavior() -> CommandResult:
        return await bonsai_app.run_async()

    async def ephys() -> None:
        client = await open_ephys.launch()
        try:
            await record_until_behavior_ends(client)
        finally:
            await open_ephys.close()  # also runs on retry, stop and abort

    return await concurrently(behavior=behavior, ephys=ephys)  # {"behavior": ..., "ephys": None}
```

- A failed branch offers **Retry**, **Continue without** (result `None`) or **Stop all**.
- A cancelled branch has 30 s to run its `finally` block.

## 7. Run it

```bash
clabe run                                       # runs main.py; add --experiment-name if it declares several workflows
clabe check my_experiments/ --strict            # for CI: checks wiring and annotations without running anything
```

From Python: `Launcher(settings=...).run_experiment(wf)`.

## 8. Derive a variant

```python
fip = wf.variant("vr-foraging-fip").insert_after("rig", fip_rig).replace("qc", my_qc)
```

See [examples/behavior_launcher.py](https://github.com/AllenNeuralDynamics/clabe/blob/main/examples/behavior_launcher.py)
for a complete workflow.
