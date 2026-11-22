"""A one-minute tour of clabe: a session declared as steps, answered from the terminal or the browser.

Run it with ``scripts/demo.ps1`` (with the hub), or ``uv run clabe run examples/demo/main.py --allow-dirty``.
"""

import asyncio
import random
import tempfile
import time
from pathlib import Path
from typing import Literal

from aind_behavior_services.session import Session
from pydantic import BaseModel, Field

from clabe import checks, steps, ui
from clabe.workflow import Workflow, concurrently

OUTPUT = Path(tempfile.gettempdir()) / "clabe-demo"

wf = Workflow("clabe-demo", exit_prompt_timeout=None)


class Rig(BaseModel):
    """This computer, as a rig: where its sessions' data go."""

    name: str = "demo-rig"
    data_directory: Path = OUTPUT / "data"


class Task(BaseModel):
    """The task's settings: a form, built from this model, validated wherever it is answered."""

    trials: int = Field(default=40, ge=5, le=200, description="Trials to run")
    reward_ul: float = Field(default=3.0, gt=0, le=10, title="Reward (µL)")
    stage: Literal["habituation", "training", "expert"] = "training"
    calibration: Literal["measure", "reuse"] = Field(default="measure", description="Measure the valves, or reuse")


@wf.step(title="Welcome")
def welcome() -> None:
    ui.markdown(
        "## The clabe demo\n\n"
        "Each step is a Python function. Answer here **or in the browser**: the first answer wins, "
        "and the hub shows this run too.\n\n"
        "- Click a finished step to **go back** to it: only what depends on it runs again.\n"
        "- The calibration fails once: **Retry** it.\n",
        title="Welcome",
    )


wf.step(steps.session(experimenter_validator=None))  # who runs it, on which animal (remembered for next time)


@wf.step
def rig() -> Rig:
    return Rig()


@wf.step(title="Task settings")
def task() -> Task:
    return ui.form(Task, title="Task settings")


wf.step(steps.review("session", "rig", "task", back_to="task"))
wf.step(
    steps.preflight(
        checks.free_space(100e6),  # on the rig's data drive
        checks.that(lambda task: task.trials * task.reward_ul <= 1000, "That is more than 1 mL of water."),
    )
)
wf.step(steps.register())  # creates the session folder; a barrier: nothing before it runs again

_attempts = {"calibrate": 0}


@wf.step(title="Calibrate the valves", when=lambda task: task.calibration == "measure")
def calibrate() -> float:
    _attempts["calibrate"] += 1
    with ui.activity("Measuring the valves"):
        time.sleep(1.5)
    if _attempts["calibrate"] == 1:
        raise RuntimeError("Valve 2 did not respond. Check its tubing, then Retry.")
    return 3.1


@wf.step(title="Last calibration", when=lambda task: task.calibration == "reuse")
def last_calibration() -> float:
    ui.notify("Reusing yesterday's calibration (3.0 µL per opening).")
    return 3.0


@wf.step(title="Acquire", cancellable=True)
async def acquire(task: Task, calibrate: float | None, last_calibration: float | None) -> list[int]:
    volume = calibrate if calibrate is not None else last_calibration
    ui.notify(f"Valves at {volume} µL per opening; {task.trials} trials.")
    rng = random.Random(42)
    licks: list[int] = []

    async def behavior() -> int:
        with ui.activity("Behavior: running trials"):
            for trial in range(task.trials):
                licks.append(max(0, int(rng.gauss(4 + 6 * trial / task.trials, 2))))
                await asyncio.sleep(3 / task.trials)
        return sum(licks)

    async def camera() -> int:
        with ui.activity("Camera: recording"):
            await asyncio.sleep(3)
        return 180

    results = await concurrently(behavior=behavior, camera=camera)
    ui.notify(f"{results['behavior']} licks; {results['camera']} frames recorded.")
    return licks


@wf.step(title="Licks per trial")
def plot(session: Session, register: Path, acquire: list[int]) -> Path:
    path = register / "licks.svg"
    path.write_text(_bars(acquire, f"{session.subject}: licks per trial"), encoding="utf-8")
    ui.image(path, title="Licks per trial", caption="Drawn by the step, shown in every client.")
    return path


@wf.step(title="Session report", optional="Show the session report?")
def report(session: Session, task: Task, acquire: list[int]) -> None:
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in task.model_dump().items())
    ui.html(
        f"""<style>body{{font-family:sans-serif;margin:1.5rem}}td{{padding:.2rem .8rem;border-bottom:1px solid #ddd}}
        .kpi{{font-size:2rem;font-weight:700;color:#8146e1}}</style>
        <h2>{session.subject}</h2><p class="kpi">{sum(acquire)} licks</p>
        <p>{len(acquire)} trials, {sum(acquire) / max(1, len(acquire)):.1f} licks per trial.</p>
        <table>{rows}</table>""",
        title="Session report",
    )


@wf.step(title="Done")
def done(register: Path) -> None:
    ui.notify(f"Done. The session, its figure and its records are in {register}.")


def _bars(values: list[int], title: str) -> str:
    """A bar chart as SVG (no plotting library needed)."""
    width, height, top = 640, 300, 40
    peak = max(values) or 1
    bar = width / len(values)
    bars = "".join(
        f'<rect x="{i * bar + 1:.1f}" y="{height - v / peak * (height - top - 20):.1f}" width="{bar - 2:.1f}" '
        f'height="{v / peak * (height - top - 20):.1f}" fill="#8146e1"/>'
        for i, v in enumerate(values)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height + 20}" width="{width}">'
        f'<rect width="100%" height="100%" fill="#f3f0e9"/>'
        f'<text x="12" y="26" font-family="sans-serif" font-size="18" fill="#141d28">{title}</text>'
        f'{bars}<line x1="0" y1="{height}" x2="{width}" y2="{height}" stroke="#141d28"/></svg>'
    )
