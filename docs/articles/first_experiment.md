# Your first experiment

Write a workflow, run it, answer its prompts from a browser and find what it recorded.

## 1. Install

```bash
uv init my-experiments && cd my-experiments
uv add "aind-clabe[frontend]"   # the terminal UI and the browser page; plain aind-clabe asks in the console
git init && git add -A && git commit -m "start"   # clabe records the commit each run used
```

## 2. Declare the steps

```python
# main.py (uv init made one: replace it)
from pathlib import Path

from pydantic import BaseModel

from clabe import steps, ui
from clabe.workflow import Workflow


class Rig(BaseModel):
    name: str
    data_directory: Path


class Task(BaseModel):
    trials: int = 100
    reward_ul: float = 3.0


wf = Workflow("hello")
wf.step(steps.session(experimenter_validator=None))  # who is running, on which animal


@wf.step
def rig() -> Rig:
    return Rig(name="rig-1", data_directory=Path.home() / "data")


@wf.step
def task() -> Task:
    return ui.form(Task, title="Task")  # a form from the model
```

- A step's name is the name of its output.
- A step's parameters are the earlier outputs it needs.

## 3. Run it

```bash
uv run clabe run --allow-dirty   # runs main.py
```

- The terminal shows the prompts.
- The printed address shows the same prompts in the browser. You can answer in either one.

## 4. Review, check, register

```python
from clabe import checks

wf.step(steps.review("session", "rig", "task", back_to="task"))  # "Go back" re-asks the task
wf.step(steps.preflight(checks.free_space(1e9)))  # on failure, lists every problem
wf.step(steps.register())  # creates the session folder; a barrier
```

- **Go back** re-runs a step and only the steps that depend on it.
- After a barrier has run, no step before it can run again.

## 5. Handle failures

```python
import random


@wf.step(title="Calibrate")
def calibrate(rig: Rig) -> float:
    if random.random() < 0.5:
        raise RuntimeError(f"{rig.name}: the valve did not respond.")
    return 4.2


@wf.step(title="Acquire", barrier=True)
def acquire(task: Task, calibrate: float, register: Path) -> None:
    ui.notify(f"Running {task.trials} trials into {register}")
```

- A step that raises pauses the run with **Retry / Go back / Abort**.
- With `on_error="warn"`, a failure only shows a warning.
- With `optional="Run it?"`, the run asks before running the step.

## 6. Find the records

```text
<data_directory>/<session>/Behavior/Logs/.launcher/
  events.jsonl     every prompt, answer and step
  outputs.jsonl    every step's output and its inputs
  run.json         code, environment, machine, configuration
  launcher.log
```

Replay the session with the same answers:

```bash
uv run clabe replay <session>/Behavior/Logs/.launcher/events.jsonl main.py --allow-dirty
```

## 7. Check it in CI

```bash
uv run clabe check main.py --strict   # checks wiring and types without running anything
```

## 8. Put it on the desktop

```bash
uv run clabe shortcut --name "Hello"   # an icon that runs main.py in a terminal
```

On a rig with the [daemon](daemon.md), a run started this way also appears in the hub.

## Next

- [Write a workflow](workflows.md): step options, conditions, branches and variants.
- [Reusable steps and checks](reusable_steps.md): share steps across experiments.
- [examples/behavior_launcher.py](https://github.com/AllenNeuralDynamics/clabe/blob/main/examples/behavior_launcher.py):
  a complete workflow.
