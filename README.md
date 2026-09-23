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

To work on this repository locally:

```bash
git clone https://github.com/AllenNeuralDynamics/clabe.git
cd clabe
uv sync
```

## What CLABE provides

- **Experiment launcher** — discover and run functions marked with `@experiment`, with console, TUI, and web-served interaction options.
- **Frontends and forms** — collect typed input from Pydantic models, prompt for paths, confirmations, selections, and read-only reviews.
- **Applications and executors** — describe external commands once and run them locally, asynchronously, detached, or through XML-RPC.
- **Stores and services** — compose local, in-memory, Ficus, and optional Dataverse-backed configuration and data services.
- **Operational helpers** — resource constraints, data transfer, structured logging, OpenTelemetry support, and session construction.
- **Repository state** — capture a JSON snapshot of a repository and its submodules for dataset or experiment metadata.

## Quick start

Define an experiment in a Python module:

```python
from clabe.launcher import Launcher, experiment


@experiment()
async def my_experiment(launcher: Launcher) -> None:
    launcher.frontend.notify("Experiment started")
    # Configure the session, check resources, and run applications here.
```

Run the module with the CLABE CLI:

```bash
uv run clabe run path/to/my_experiment.py
```

See [examples/behavior_launcher.py](examples/behavior_launcher.py) for a fuller example that combines sessions, forms, stores, resource checks, applications, and telemetry.

## Command line

Show available commands and their options:

```bash
uv run clabe --help
```

Run an experiment interactively:

```bash
uv run clabe run path/to/my_experiment.py
```

Serve an experiment's text UI locally:

```bash
uv run clabe serve path/to/my_experiment.py --port 8089
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
uv add "aind-clabe[web]"            # Web-served Textual UI
uv add "aind-clabe[otel]"           # OpenTelemetry exporters and YAML support
```

## Documentation and development

- Browse the [documentation](https://allenneuraldynamics.github.io/clabe/) for API references and guides.
- Run tests with `uv run pytest`.
- Check style with `uv run ruff check`.
- Build documentation locally with `uv run mkdocs serve` after installing the `docs` dependency group.

Contributions are welcome. Please include focused tests for behavior changes and keep the package's cross-platform support in mind.
