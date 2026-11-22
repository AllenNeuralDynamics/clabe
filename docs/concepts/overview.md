# The big picture

CLABE runs **behavior experiments on rigs**. A session is a sequence of things a person and a machine do together:
pick the animal, load its training stage and the rig's configuration, check them, start the task programs (Bonsai,
Open Ephys, ...), then evaluate the curriculum, map metadata, run QC and hand the data off. CLABE's job is to make
that sequence explicit, interactive from anywhere, recoverable when something fails, and recorded well enough to
reproduce.

## The pieces

```text
experiment repository              rig PC
─────────────────────              ─────────────────────────────────────────────
Workflow (steps) ─── clabe run ──► Launcher ──► Run ──► records on disk
                                                 │      (events, outputs, run.json)
                                                 │ prompts, events
                                  ┌──────────────┴───────────────┐
                                  ▼                              ▼
                            TUI / console               run server (HTTP)
                                                                 ▲
                                               daemon: /run/, worker role
                                                                 ▲
                          ┌──────────────────────────────────────┼─────────┐
                    web browser                           hub (Docker)    agents
```

- A **[workflow](workflows.md)** declares the experiment: an ordered list of **steps**, each a plain Python
  function. Steps receive what earlier steps returned, by parameter name.
- The **launcher** prepares the machine (repository checks, logging, the session folder) and executes the workflow
  as a **[run](runs.md)**.
- The run executes the steps one by one. When a step needs the user it opens a **[prompt](prompts.md)**; every
  connected **client** — the terminal UI, the web page, an agent — sees it, and the first valid answer wins.
- Everything that happens is an **[event](records.md)**, appended to the run's event log and streamed to clients;
  step outputs, artifacts and a manifest of code, environment and machine are written next to it.
- Each run is served over HTTP by its **run server**. On a rig, the **[daemon](machines.md)** gives the machine one
  stable address, starts experiments from the browser, and runs jobs for runs hosted on other rigs. The optional
  **hub** follows a whole fleet of rigs, and **agents** can drive runs through MCP or AG-UI.

## Design principles

**One run, many clients.** A run never belongs to a screen. The TUI on the rig, a browser across the building and an
agent are all clients of the same run; they see the same events and any of them may answer. Validation happens in
the run, so every client gets the same rules and the same error messages.

**Declared, not scripted.** Because steps are declared with their inputs, the run knows which steps depend on which.
Going back to a step re-runs only what depends on it; a failed step can be retried alone; the UI can show the whole
session before it happens; CI can check the wiring of every experiment without running anything (`clabe check`).

**Rig-first topology.** Everything needed to run a session lives on the rig: the launcher, the run server, the
daemon. The hub is an optional observer that dials in; if it is down, rigs keep working.

**Recorded by default.** A session's decisions (every prompt and answer, who answered), its inputs and outputs
(per attempt, with lineage and hashes) and its context (commits, diffs, environment, machine) are written without the
experiment asking for it.

**Plain Python.** A step is a function; a workflow is a list of them; a prompt is a function call that returns a
typed value. Steps can be tested by calling them.

## A session, end to end

1. `clabe run experiments/vr_foraging.py` (or **Start** on the rig's daemon page) builds the launcher, which checks the
   repository and starts logging, and loads the workflow.
2. The run starts and is served (`http://<rig>:8070/run/` through the daemon). It writes `run.json`.
3. Step `session` asks who is running and which animal: the TUI and every open browser show the prompt; the
   experimenter answers on the rig.
4. Steps `trainer_state` and `rig` load configuration from the stores; step `review` shows it; the experimenter
   clicks **Go back** — the run re-runs `session` and only the steps depending on it.
5. Step `register` (a **barrier**) creates the session folder; from now on nothing before it can run again.
6. Step `acquire` runs Bonsai and Open Ephys as **branches**; Open Ephys fails to find its probe; only that branch
   pauses with *Retry / Continue without / Stop all* while Bonsai keeps running. The experimenter retries it from a
   laptop via the hub's inbox.
7. The remaining steps run (curriculum, mapping, QC, transfer), the run ends, and the logs, events, outputs and
   manifest are copied into the session folder.
