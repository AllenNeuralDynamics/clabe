# Your first experiment

Write a workflow, run it, answer it from a browser and find what it recorded.

## 1. Install

```bash
uv init my-experiments && cd my-experiments
uv add aind-clabe
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

- A step's **name** is the name of what it returns.
- A step's **parameters** are the earlier outputs it needs.

## 3. Run it

```bash
uv run clabe run --allow-dirty   # runs main.py
```

- The terminal shows the prompts.
- Open the printed address: the same prompts are in the browser. Answer in either.

## 4. Review, check, register

```python
from clabe import checks

wf.step(steps.review("session", "rig", "task", back_to="task"))  # "Go back" re-asks the task
wf.step(steps.preflight(checks.free_space(1e9)))  # fails listing every problem
wf.step(steps.register())  # creates the session folder; a barrier
```

- **Go back** re-runs a step and only what depends on it.
- After a **barrier**, nothing before it can run again.

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
- `on_error="warn"` only warns; `optional="Run it?"` asks first.

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
uv run clabe check main.py --strict   # wiring and types, nothing runs
```

## 8. Put it on the desktop

```bash
uv run clabe shortcut --name "Hello"   # an icon that runs main.py in a terminal
```

On a rig with the [daemon](daemon.md), a run started this way shows up in the hub too.

## Next

- [Write a workflow](workflows.md): everything a step can do.
- [Reusable steps and checks](reusable_steps.md): share steps across experiments.
- [examples/behavior_launcher.py](https://github.com/AllenNeuralDynamics/clabe/blob/main/examples/behavior_launcher.py):
  a complete workflow.
