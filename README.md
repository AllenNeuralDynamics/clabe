<div align="center">

<img src="docs/logo.svg" width="96" alt="clabe">

# clabe

**Behavior experiments as declared workflows: run them on the rig, answer them from anywhere, and record everything.**

[![Documentation](https://img.shields.io/badge/documentation-blue)](https://allenneuraldynamics.github.io/clabe/)
[![CI](https://github.com/AllenNeuralDynamics/clabe/actions/workflows/clabe.yml/badge.svg)](https://github.com/AllenNeuralDynamics/clabe/actions/workflows/clabe.yml)
[![PyPI version](https://img.shields.io/pypi/v/aind-clabe)](https://pypi.org/project/aind-clabe/)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)

</div>

A behavior session is a sequence of things a person and a rig do together: pick the animal, load its training stage
and the rig's configuration, check them, start Bonsai and Open Ephys, then run the curriculum, QC and hand the data
off. clabe turns that sequence into plain Python steps — and gives you the rest for free.

## Why clabe

- **Write steps, not plumbing.** Each step is a function; its parameters name what it needs. No global state, no
  hand-written menus or confirmation loops.
- **Go back without starting over.** Change the rig and only what depends on it runs again. Failed steps offer
  Retry / Go back / Abort; what must not run twice (registering the session, starting acquisition) is a barrier.
- **Answer from anywhere.** The terminal on the rig, a browser in the corridor and an AI agent all see the same
  prompts; the first valid answer wins. Forms come from your Pydantic models.
- **Run several things at once.** Bonsai, Open Ephys and a camera in one step; each fails, retries and stops on its
  own.
- **Two PCs, one session.** Run apps on a second rig PC as if they were local; clabe checks it runs the same commit and
  leases it for the session.
- **Recorded by default.** Every prompt and answer, every step's inputs and outputs, every program run with its
  hash, the configuration used, and the code, environment and machine behind it. Replay a session with
  `clabe replay`.
- **A fleet, not a pile of PCs.** A daemon on each rig starts experiments from the browser; an optional hub shows
  every rig and every run waiting for an answer. Install a rig with one line, over SSH.
- **Checked in CI.** `clabe check --strict` verifies every workflow's wiring and types without running anything.

## A taste

```python
from clabe import checks, steps, ui
from clabe.launcher import Launcher
from clabe.workflow import Workflow, concurrently

wf = Workflow("vr-foraging")
wf.step(steps.session())                                  # who is running, on which animal


@wf.step
def trials() -> float:
    return ui.number("Trials", minimum=1, maximum=500)   # validated wherever it is answered


wf.step(steps.review("session", "trials", back_to="session"))
wf.step(steps.preflight(checks.free_space(200e9)))       # every problem reported at once
wf.step(steps.register())                                 # a barrier: the session folder exists now


@wf.step(title="Acquire", cancellable=True)
async def acquire(launcher: Launcher, trials: float) -> dict:
    return await concurrently(behavior=run_bonsai, ephys=run_open_ephys)
```

```bash
uv run clabe run my_experiment.py     # the terminal UI here, the web UI at the printed address
```

## Install

```bash
uv add aind-clabe                       # in an experiment repository
uv add "aind-clabe[aind-services]"      # + AIND services, data schema and transfer tooling
uv add "aind-clabe[otel]"               # + OpenTelemetry
uv add "aind-clabe[agents]"             # + MCP server for AI agents (clabe mcp)
```

The daemon on a rig PC (PowerShell; see [Deploy a rig](https://allenneuraldynamics.github.io/clabe/articles/deployment/)):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

## Learn more

The [documentation](https://allenneuraldynamics.github.io/clabe/) has tutorials — your first workflow, reusable steps
and checks, two-PC sessions, deploying rigs, migrating an existing experiment — and the API reference.
[examples/behavior_launcher.py](examples/behavior_launcher.py) is a complete workflow to start from.

## Development

```bash
git clone https://github.com/AllenNeuralDynamics/clabe.git && cd clabe
uv sync
uv run pytest
uv run ruff check
uv run docs/build.py && uv run zensical serve   # the docs, with the docs dependency group
```

The web UI (`ui-web/`, Blazor) is built into `src/clabe/server/static` by `uv run python scripts/build_web.py`; its
models are generated from the APIs with `uv run python scripts/openapi.py` (a test fails when they are stale).

Contributions are welcome: include focused tests for behavior changes and keep Windows and Linux working.
