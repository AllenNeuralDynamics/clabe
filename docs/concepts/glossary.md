# Glossary

**Abort** — End a run now, from any client; open prompts are cancelled and busy steps interrupted. `Abort("reason")`
raised in a step does the same.

**Activity** — Something long the user should see is going on (an app running), shown with a spinner.

**AG-UI** — A protocol for agent UIs; every run speaks it at `/api/agui`.

**Answer schema** — The JSON Schema of a valid answer to a prompt, published with it.

**Artifact** — A file a step saved with its output (`export_artifact`), under `artifacts/<step>/<attempt>/`.

**Attempt** — One execution of a step; going back and retrying create new ones.

**Barrier** — A step after which neither it nor anything before it can run again.

**Branch** — One of several things a step runs at the same time (`concurrently`); fails, retries and stops on its own.

**Built-in** — A name every step can take as a parameter without a step producing it: `launcher`, `run`.

**Client** — Anything that shows prompts and collects answers: the TUI, the console, the web UI, an agent.

**Codec** — How a step's output is recorded in `outputs.jsonl`.

**Command** — A request to change a run's course: answer, go back, retry, skip, abort.

**Daemon** — The service on every rig PC: starts experiments (host) and runs jobs for other rigs (worker).

**Event** — A record of something that happened in a run; the event log is the run's history and its live feed.

**Go back** — Re-run a step and the steps depending on it.

**Headless** — A run (or client) with no screen; its prompts are answered through the run server.

**Hub** — The optional fleet service: every rig's status and every waiting prompt.

**Launcher** — Prepares the machine for a session and executes its workflow as a run.

**Lease** — A run's reservation of a worker machine; others are refused until it is released or expires.

**Manifest** — `run.json`: the code, environment, machine, configuration and outcome of a run.

**MCP** — The Model Context Protocol; `clabe mcp` lets an MCP agent read and drive runs.

**Optional step** — A step the run asks about first; declining skips it.

**Output** — What a step returned; later steps receive it through a parameter named after the step.

**Prompt** — A question a run is waiting on; opened from a request, answered by any client.

**Request** — The description of a prompt: what to show, the answer schema, how to validate.

**Run** — One execution of a workflow: its progress, outputs, open prompt and events.

**Run server** — The HTTP API (and web UI) serving one run.

**Session** — In `clabe.ui`: whatever `ui.ask` asks right now (the run, or the launcher's client). In data terms: one
recording of one animal, with its session folder.

**Snapshot** — A run's current state, with the sequence number of the last event it reflects.

**Step** — A function of a workflow; its name names its output, its parameters name its inputs.

**Worker** — The daemon's role of running commands and keeping files for a run hosted on another machine.

**Workflow** — An experiment declared as an ordered list of steps.
