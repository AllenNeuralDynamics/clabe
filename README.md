<div align="center">

<img src="docs/logo.svg" width="220" alt="clabe">

<br>

[![Documentation](https://img.shields.io/badge/documentation-blue)](https://allenneuraldynamics.github.io/clabe/)
[![CI](https://github.com/AllenNeuralDynamics/clabe/actions/workflows/clabe.yml/badge.svg)](https://github.com/AllenNeuralDynamics/clabe/actions/workflows/clabe.yml)
[![PyPI version](https://img.shields.io/pypi/v/aind-clabe)](https://pypi.org/project/aind-clabe/)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)

</div>

clabe runs behavior experiments. You write a session as a list of Python steps: choose the animal, load its training
stage and the rig's configuration, check the hardware, start Bonsai and Open Ephys, hand the data off. clabe runs
those steps on the rig and asks the experimenter for what it needs, in the terminal or in a browser.

Each step is a function, and its parameters name the earlier steps it uses. That wiring is what makes going back
cheap: if the experimenter changes the rig configuration halfway through, only the steps that depend on it run
again. A step that fails can be retried, and steps that must never run twice, such as registering the session, lock
everything before them.

Prompts are declared once and answered anywhere. The same question appears in the terminal on the rig, in a browser
on a phone in the corridor, and to an AI agent through MCP; the first valid answer is kept. Forms are built from your
Pydantic models and validated wherever they are filled in.

Every session leaves a record next to its data: each prompt and answer, each step's inputs and outputs, every command
it ran, the configuration it used, the repository's commit and lockfile, and the machine. `clabe replay` runs a
session again from that record.

When you have more than one rig, a small daemon on each PC lets you start experiments from a browser, and an
optional hub shows every rig, every session in progress and every run waiting for an answer on one page.

## Example

```python
# main.py
from clabe import checks, steps, ui
from clabe.launcher import Launcher
from clabe.workflow import Workflow, concurrently

wf = Workflow("vr-foraging")
wf.step(steps.session())  # the experimenter and the animal


@wf.step
def trials() -> float:
    return ui.number("Trials", minimum=1, maximum=500)


wf.step(steps.review("session", "trials", back_to="session"))
wf.step(steps.preflight(checks.free_space(200e9)))  # reports every failed check at once
wf.step(steps.register())  # creates the session folder; the steps above are locked from here on


@wf.step(title="Acquire", cancellable=True)
async def acquire(launcher: Launcher, trials: float) -> dict:
    async with open_ephys_recording():  # records while the apps run
        return await concurrently(behavior=run_bonsai, fip=run_fip)
```

```bash
uv run clabe run        # runs main.py in this terminal and serves it in the browser
uv run clabe shortcut   # puts a desktop icon on the rig that does the same
```

## Try it

From a checkout of this repository, with [uv](https://docs.astral.sh/uv/) installed:

```powershell
powershell -ExecutionPolicy ByPass -File scripts/demo-local.ps1
```

This runs [examples/demo/main.py](examples/demo/main.py), a one-minute session that you can answer in the terminal
or in the browser tab it opens. `scripts/demo.ps1` runs the same session with a daemon and a hub, and needs Docker.

## Install

```bash
uv add "aind-clabe[frontend]"         # in an experiment repository
uv add "aind-clabe[aind-services]"    # AIND data schema, data transfer and watchdog
uv add "aind-clabe[otel]"             # sending logs to OpenTelemetry
uv add "aind-clabe[agents]"           # the MCP server (clabe mcp)
```

The `frontend` extra adds the terminal UI and the browser page (and the web server the daemon and the hub run on).
Without it, clabe asks its questions as plain console prompts and the rest works the same.

To install the daemon on a rig PC, see [Deploy a rig](https://allenneuraldynamics.github.io/clabe/articles/deployment/).

## Documentation

The [documentation](https://allenneuraldynamics.github.io/clabe/) starts with a tutorial that builds a first
workflow, then covers reusable steps, running on two PCs, deploying rigs, migrating an existing experiment, and the
API. [examples/behavior_launcher.py](examples/behavior_launcher.py) is a complete workflow to start from.

## Development

```bash
git clone https://github.com/AllenNeuralDynamics/clabe.git && cd clabe
uv sync
uv run pytest
uv run ruff check
uv run docs/build.py && uv run zensical serve   # the documentation, from the docs dependency group
```

The web UI in `ui-web/` is a Blazor app. `uv run python scripts/build_web.py` builds it into
`src/clabe/server/static`, and `uv run python scripts/openapi.py` regenerates its models from the APIs (a test fails
when they are out of date).
