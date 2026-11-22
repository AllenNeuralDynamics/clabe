# Glossary

## Workflows

| Term | Meaning |
|---|---|
| **Workflow** | An experiment: an ordered list of steps. |
| **Step** | A function of a workflow; its name names its output, its parameters name its inputs. |
| **Output** | What a step returned; later steps get it through a parameter of that name. |
| **Built-in** | A name any step can take without a step producing it: `launcher`, `run`. |
| **Barrier** | A step after which neither it nor anything before it can run again. |
| **Optional step** | A step the run asks about first; declining skips it. |
| **Conditional step** | A step with `when=`: runs only when its condition on earlier outputs holds. |
| **Branch** | One of several things a step runs at once (`concurrently`); fails and retries on its own. |
| **Check** | A function that raises when something is wrong; `steps.preflight` runs several at once. |
| **Codec** | How a step's output is recorded. |

## Runs

| Term | Meaning |
|---|---|
| **Launcher** | Prepares the machine and executes a workflow as a run. |
| **Run** | One execution of a workflow. |
| **Attempt** | One execution of a step; going back and retrying create new ones. |
| **Go back** | Re-run a step and the steps depending on it. |
| **Abort** | End a run now (from any client, or `raise Abort(...)` in a step). |
| **Command** | Answer, go back, retry, skip, abort. |
| **Event** | One thing that happened in a run; the event log is its history and live feed. |
| **Snapshot** | A run's current state, with the last event it reflects. |
| **Artifact** | A file a step saved with its output (`export_artifact`). |
| **Manifest** | `run.json`: code, environment, machine, configuration and outcome. |
| **Replay** | Running again with a recorded session's answers (`clabe replay`). |

## Prompts and clients

| Term | Meaning |
|---|---|
| **Request** | What to ask: what to show, the answer schema, how to validate. |
| **Prompt** | A request the run is waiting on; any client may answer it. |
| **Answer schema** | The JSON Schema of a valid answer. |
| **Client** | Shows prompts and collects answers: TUI, console, web UI, agent. |
| **Session** | In `clabe.ui`: who `ui.ask` asks right now. In data: one recording of one animal. |
| **Activity** | Something long going on (an app running), shown with a spinner. |
| **Headless** | A run with no screen; answered through its run server. |

## Machines

| Term | Meaning |
|---|---|
| **Run server** | The HTTP API and web UI of one run. |
| **Daemon** | The service on each rig PC: starts experiments (host), runs jobs for other rigs (worker). |
| **Worker** | The daemon running commands for a run hosted on another machine. |
| **Lease** | A run's reservation of a worker. |
| **Hub** | The optional fleet view: every rig and every waiting prompt. |
| **MCP** | Model Context Protocol; `clabe mcp` lets an agent drive runs. |
| **AG-UI** | An agent-UI protocol; every run speaks it at `/api/agui`. |
