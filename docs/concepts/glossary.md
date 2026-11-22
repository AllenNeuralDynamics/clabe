# Glossary

## Workflows

| Term | Meaning |
|---|---|
| **Workflow** | An experiment: an ordered list of steps. |
| **Step** | A function in a workflow. Its name is the name of its output, and its parameters name its inputs. |
| **Output** | The value a step returned. Later steps receive it through a parameter with the step's name. |
| **Built-in** | A name any step can take without a step producing it: `launcher`, `run`. |
| **Barrier** | A step after which neither it nor anything before it can run again. |
| **Optional step** | A step the run asks about first; declining skips it. |
| **Conditional step** | A step with `when=`: runs only when its condition on earlier outputs holds. |
| **Branch** | One of several functions a step runs at once with `concurrently`. Each branch fails and is retried on its own. |
| **Check** | A function that raises when its condition is not met. `steps.preflight` runs several checks in one step. |
| **Codec** | How a step's output is recorded. |

## Runs

| Term | Meaning |
|---|---|
| **Launcher** | Prepares the machine and executes a workflow as a run. |
| **Run** | One execution of a workflow. |
| **Attempt** | One execution of a step. Going back or retrying starts a new attempt. |
| **Go back** | Re-run a step and the steps depending on it. |
| **Abort** | End a run now (from any client, or `raise Abort(...)` in a step). |
| **Command** | Answer, go back, retry, skip, abort. |
| **Event** | A record of one occurrence in a run. The event log is the run's history and its live feed. |
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
| **Session** | In `clabe.ui`: the client that `ui.ask` currently asks. In data: one recording of one animal. |
| **Activity** | A long-running operation (an app running), shown with a spinner. |
| **Headless** | A run with no screen. Its prompts are answered through its run server. |

## Machines

| Term | Meaning |
|---|---|
| **Run server** | The HTTP API and web UI of one run. |
| **Daemon** | The service on each rig PC. It starts experiments (as host) and runs jobs for other rigs (as worker). |
| **Worker** | The daemon running commands for a run hosted on another machine. |
| **Lease** | A run's reservation of a worker. |
| **Hub** | The optional fleet view: every rig and every waiting prompt. |
| **MCP** | Model Context Protocol; `clabe mcp` lets an agent drive runs. |
| **AG-UI** | An agent-UI protocol; every run speaks it at `/api/agui`. |
