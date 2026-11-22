---
hide:
  - navigation
---

<div class="clabe-hero" markdown>

# clabe<span class="clabe-slash">/</span>

<p class="lead">Write a behavior session as Python steps. Run it on the rig, answer it from anywhere, record everything.</p>

[Get started](articles/first_experiment.md){ .md-button .md-button--primary }
[Install a rig](articles/deployment.md){ .md-button }
[GitHub](https://github.com/AllenNeuralDynamics/clabe){ .md-button }

</div>

<div class="grid cards" markdown>

-   :material-rocket-launch-outline:{ .lg .middle } **First experiment**

    ---

    Write a workflow, run it, answer it from a browser, find its records.

    [:octicons-arrow-right-24: Start here](articles/first_experiment.md)

-   :material-source-branch:{ .lg .middle } **Write a workflow**

    ---

    Steps, going back, barriers, failures, branches, reusable steps and checks.

    [:octicons-arrow-right-24: Workflows](articles/workflows.md)

-   :material-server-network:{ .lg .middle } **Run it on rigs**

    ---

    One-line install, two-PC sessions, the fleet hub.

    [:octicons-arrow-right-24: Deploy a rig](articles/deployment.md)

-   :material-sitemap-outline:{ .lg .middle } **Concepts**

    ---

    How the pieces relate, in diagrams; and the glossary.

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
    return ui.number("Trials", minimum=1, maximum=500)  # validated wherever it is answered


wf.step(steps.review("session", "trials", back_to="session"))
wf.step(steps.register())  # a barrier: the session folder exists now


@wf.step(title="Acquire", cancellable=True)
async def acquire(launcher: Launcher, trials: float) -> dict:
    return await concurrently(behavior=run_bonsai, ephys=run_open_ephys)  # retried and stopped one by one
```

```bash
uv run clabe run                           # main.py: the TUI here, the web UI at the printed address
```

## Install

```bash
uv add aind-clabe                      # in an experiment repository
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/AllenNeuralDynamics/clabe/main/scripts/install.ps1 | iex"
```

The second line installs the daemon on a rig PC; see [Deploying](articles/deployment.md).
