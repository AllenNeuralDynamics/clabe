---
hide:
  - navigation
---

<div class="clabe-hero" markdown>

# clabe<span class="clabe-slash">/</span>

<p class="lead">Behavior experiments as declared workflows. Write a session as plain Python steps; clabe runs it on the
rig, lets anyone answer it from the terminal, a browser or an agent, recovers when a step fails, and records everything
needed to reproduce it.</p>

[Get started](concepts/overview.md){ .md-button .md-button--primary }
[Install a rig](articles/deployment.md){ .md-button }
[GitHub](https://github.com/AllenNeuralDynamics/clabe){ .md-button }

</div>

## What it does

- **Declared, not scripted.** A session is a list of steps wired by name. Go back to any step and only what depends on
  it runs again; a failed step is retried on its own; what must not run twice is a barrier.
- **One run, many clients.** The TUI on the rig, the web page in the corridor and an AI agent answer the same prompts;
  the first valid answer wins, and every client sees it.
- **Recorded by default.** Every prompt and answer, each step's inputs and outputs, the programs it ran with their
  hashes, the configuration it used, and the code, environment and machine behind it.
- **Rigs and fleets.** A daemon on each rig PC starts experiments from the browser and runs jobs for the other rigs of
  a session; an optional hub shows the whole fleet and every run waiting for an answer.

<div class="grid cards" markdown>

-   :material-lightbulb-on-outline:{ .lg .middle } **Concepts**

    ---

    Workflows, runs, prompts and clients, records, machines: how clabe is put together, and why.

    [:octicons-arrow-right-24: The big picture](concepts/overview.md)

-   :material-source-branch:{ .lg .middle } **Write a workflow**

    ---

    Steps, wiring, going back, barriers, failures, branches, shared steps and static checks in CI.

    [:octicons-arrow-right-24: Workflows](articles/workflows.md)

-   :material-server-network:{ .lg .middle } **Run it on rigs**

    ---

    One line installs the daemon on a rig PC; deploy and update over SSH; follow the fleet from the hub.

    [:octicons-arrow-right-24: Deploying](articles/deployment.md)

-   :material-code-braces:{ .lg .middle } **Reference**

    ---

    Every module, class and function, from the source.

    [:octicons-arrow-right-24: API reference](api/workflow/workflow.md)

</div>

## A taste

```python
from clabe import steps, ui
from clabe.launcher import Launcher
from clabe.workflow import Workflow, concurrently

wf = Workflow("vr-foraging")
wf.step(steps.session())  # who is running, on which animal


@wf.step
def trials() -> float:
    return ui.number("Trials", minimum=1, maximum=500)  # validated wherever it is answered


wf.step(steps.review("session", "trials", back_to="session"))
wf.step(steps.register())  # a barrier: the session folder exists now


@wf.step(title="Acquire", cancellable=True)
async def acquire(launcher: Launcher, trials: float) -> dict:
    return await concurrently(behavior=run_bonsai, ephys=run_open_ephys)  # retried and stopped one by one
```

```bash
uv run clabe run my_experiment.py          # the TUI here, the web UI at the printed address
```

## Install

```bash
uv add aind-clabe                      # in an experiment repository
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

The second line installs the daemon on a rig PC; see [Deploying](articles/deployment.md).
