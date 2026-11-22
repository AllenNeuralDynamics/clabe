---
hide:
  - navigation
---

<div class="clabe-hero" markdown>

# clabe<span class="clabe-slash">/</span>

<p class="lead">Write a behavior session as Python steps. The rig runs it, its prompts can be answered from the terminal, a browser or an agent, and every prompt, answer and output is recorded.</p>

[Get started](articles/first_experiment.md){ .md-button .md-button--primary }
[Install a rig](articles/deployment.md){ .md-button }
[GitHub](https://github.com/AllenNeuralDynamics/clabe){ .md-button }

</div>

<div class="grid cards" markdown>

-   :material-rocket-launch-outline:{ .lg .middle } **First experiment**

    ---

    Write a workflow, run it, answer its prompts from a browser and find its records.

    [:octicons-arrow-right-24: Start here](articles/first_experiment.md)

-   :material-source-branch:{ .lg .middle } **Write a workflow**

    ---

    Steps, going back, barriers, failures, branches, reusable steps and checks.

    [:octicons-arrow-right-24: Workflows](articles/workflows.md)

-   :material-server-network:{ .lg .middle } **Run it on rigs**

    ---

    Install the daemon on a rig PC in one line, run sessions across two PCs and follow the fleet from the hub.

    [:octicons-arrow-right-24: Deploy a rig](articles/deployment.md)

-   :material-sitemap-outline:{ .lg .middle } **Concepts**

    ---

    Diagrams of how clabe's parts relate, and a glossary.

    [:octicons-arrow-right-24: Concepts](concepts/index.md)

</div>

## A taste

```python
# main.py
from clabe import steps, ui
from clabe.launcher import Launcher
from clabe.workflow import Workflow, concurrently

wf = Workflow("vr-foraging")
wf.step(steps.session())  # who is running, on which animal


@wf.step
def trials() -> float:
    return ui.number("Trials", minimum=1, maximum=500)  # the run validates it, whichever client answers


wf.step(steps.review("session", "trials", back_to="session"))
wf.step(steps.register())  # a barrier; creates the session folder


@wf.step(title="Acquire", cancellable=True)
async def acquire(launcher: Launcher, trials: float) -> dict:
    return await concurrently(behavior=run_bonsai, ephys=run_open_ephys)  # each branch is retried or stopped on its own
```

```bash
uv run clabe run                           # runs main.py: the TUI in this terminal, the web UI at the printed address
```

## Try it

From a checkout, with uv installed, these scripts run a one-minute session in the terminal and the browser.

```powershell
powershell -ExecutionPolicy ByPass -File scripts/demo-local.ps1   # clabe only, without the daemon or the hub
powershell -ExecutionPolicy ByPass -File scripts/demo.ps1         # followed by the hub (needs Docker)
```

## Install

```bash
uv add "aind-clabe[frontend]"          # in an experiment repository; without [frontend]: console prompts only
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

The second line installs the daemon on a rig PC; see [Deploying](articles/deployment.md).
