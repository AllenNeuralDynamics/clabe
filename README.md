# clabe

<div align="center">

<pre>
 ██████╗██╗      █████╗ ██████╗ ███████╗
██╔════╝██║     ██╔══██╗██╔══██╗██╔════╝
██║     ██║     ███████║██████╔╝█████╗  
██║     ██║     ██╔══██║██╔══██╗██╔══╝  
╚██████╗███████╗██║  ██║██████╔╝███████╗
 ╚═════╝╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝

Command-line-interface Launcher for AIND Behavior Experiments
</pre>
</div>

[![Documentation](https://img.shields.io/badge/documentation-blue)](https://allenneuraldynamics.github.io/clabe/)
[![CI](https://github.com/AllenNeuralDynamics/clabe/actions/workflows/clabe.yml/badge.svg)](https://github.com/AllenNeuralDynamics/clabe/actions/workflows/clabe.yml)
[![PyPI version](https://img.shields.io/pypi/v/aind-clabe)](https://pypi.org/project/aind-clabe/)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](LICENSE)

CLABE is a Python toolkit for building, running, and operating behavioral-experiment workflows. It provides a launcher for experiment scripts alongside composable utilities for user interaction, external applications, configuration and storage, resource checks, data transfer, remote execution, logging, and repository-state capture.

The library is designed to be useful both in an interactive experiment session and in scripted or remote workflows.

## Install

For a project managed with [uv](https://docs.astral.sh/uv/):

```bash
uv add aind-clabe
```

Or install with pip:

```bash
pip install aind-clabe
```

To install the daemon on a rig PC (see [Deploying](https://github.com/AllenNeuralDynamics/clabe/blob/main/docs/articles/deployment.md)):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

To work on this repository locally:

```bash
git clone https://github.com/AllenNeuralDynamics/clabe.git
cd clabe
uv sync
```

## What CLABE provides

- **Workflows** — declare an experiment as steps wired by name; the launcher runs them with go-back, barriers, retries and optional steps, records every output, and checks the wiring statically in CI (`clabe check`).
- **Rigs and fleets** — a daemon on every rig PC starts experiments from the browser and runs jobs for other rigs; an optional hub (Docker) shows the whole fleet and every run waiting for an answer.
- **Agents** — `clabe mcp` (MCP) and an AG-UI endpoint let AI agents follow runs and answer prompts.
- **One run, many clients** — answer prompts in the TUI, the console or the web UI served by the run (Markdown, images and pages included); the first answer wins.
- **Frontends and forms** — collect typed input from Pydantic models, prompt for paths, confirmations, selections, and read-only reviews.
- **Applications and executors** — describe external commands once and run them locally, asynchronously, detached, or on another rig through its clabe daemon.
- **Multi-rig experiments** — run apps in a matching checkout on another computer (`RemoteRepository`, which leases that machine for the run), start its daemon automatically, and transfer both machines' data through one watchdog manifest. See [Running on other rigs](https://github.com/AllenNeuralDynamics/clabe/blob/main/docs/articles/remote_execution.md).
- **Stores and services** — compose local, in-memory, and optional Ficus (confierge) and Dataverse-backed configuration and data services.
- **Operational helpers** — resource constraints, data transfer, structured logging, OpenTelemetry support, and session construction.
- **Repository state** — capture a JSON snapshot of a repository and its submodules for dataset or experiment metadata.

## Quick start

New to CLABE? [The big picture](https://github.com/AllenNeuralDynamics/clabe/blob/main/docs/concepts/overview.md) explains the pieces and how a session flows through them.

Declare an experiment as a workflow in a Python module:

```python
from clabe import steps, ui
from clabe.launcher import Launcher
from clabe.workflow import Workflow

wf = Workflow("my-experiment")
wf.step(steps.session())


@wf.step
def trials() -> float:
    return ui.number("Trials", minimum=1)


@wf.step(barrier=True)
async def run_task(launcher: Launcher, trials: float) -> None:
    ui.notify(f"Running {trials:g} trials")
```

Run it with the CLABE CLI — it is served in the browser too (`--no-serve` to turn that off):

```bash
uv run clabe run path/to/my_experiment.py
```

See [examples/behavior_launcher.py](examples/behavior_launcher.py) for a fuller example that combines sessions, forms, stores, resource checks, applications, and telemetry, and [Workflows](https://github.com/AllenNeuralDynamics/clabe/blob/main/docs/articles/workflows.md) for the rules.

## Command line

Show available commands and their options:

```bash
uv run clabe --help
```

Run an experiment interactively:

```bash
uv run clabe run path/to/my_experiment.py
```

`clabe run` also serves the run over HTTP while it executes (`--no-serve` to disable): open the printed URL to
follow and answer it from a browser, or drive it from code through the API described at `/docs`.

```bash
uv run clabe run path/to/my_experiment.py --host 0.0.0.0 --token "$CLABE_TOKEN"
```

Capture Git state for a repository and all declared submodules:

```bash
uv run clabe repository-state path/to/repository > repository-state.json
```

Each repository entry includes its URL, commit SHA, exact and nearest tags, branch, dirty state, name, cwd-relative path, and nested submodules. The launcher writes this snapshot alongside its temporary output; when logs are copied, it is saved at `Behavior/Logs/.launcher/repository-state.json` in the session directory.

## Optional integrations

Some integrations are optional so a basic installation remains lightweight:

```bash
uv add "aind-clabe[aind-services]"  # AIND services, data schema, and transfer tooling
uv add "aind-clabe[otel]"           # OpenTelemetry exporters and YAML support
uv add "aind-clabe[agents]"         # MCP server for AI agents (clabe mcp)
```

## Documentation and development

- Browse the [documentation](https://allenneuraldynamics.github.io/clabe/) for API references and guides.
- Run tests with `uv run pytest`.
- Check style with `uv run ruff check`.
- Build documentation locally with `uv run mkdocs serve` after installing the `docs` dependency group.

Contributions are welcome. Please include focused tests for behavior changes and keep the package's cross-platform support in mind.
