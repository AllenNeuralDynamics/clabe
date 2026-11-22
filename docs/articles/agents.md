# Agents

An AI agent can follow a run and answer its prompts like any other client: through the run API. Two adapters make
that convenient; neither changes the run, and the first valid answer still wins.

## MCP

`clabe mcp` serves clabe to an MCP client (e.g. Claude) over stdio. Point it at a run server, a rig's
[daemon](daemon.md) or a [hub](hub.md):

```bash
uv add "aind-clabe[agents]"
clabe mcp --url http://rig-3:8070          # token from --token, $CLABE_HUB_TOKEN or $CLABE_TOKEN
```

For example, in an MCP client's configuration:

```json
{"mcpServers": {"clabe": {"command": "uvx", "args": ["--from", "aind-clabe[agents]", "clabe", "mcp", "--url", "http://hub:8080"]}}}
```

| Tool | Does |
|---|---|
| `list_rigs` | The rigs it can act on (through a hub: their status and what they wait for). |
| `get_run` | The run's steps, current step, and open prompt with its JSON answer schema. |
| `wait_for_prompt` | Waits until the run asks something (or ends). |
| `answer_prompt` | Answers the open prompt; a rejected answer returns the reason. |
| `ask_the_user` | Asks the MCP client's user (elicitation) to answer a simple prompt, then submits it. |
| `go_back`, `retry_step`, `skip_step`, `abort_run` | The run's commands. |

Through a hub, every tool takes the `rig` to act on.

## AG-UI

Every run also speaks [AG-UI](https://docs.ag-ui.com) at `POST /api/agui` (through a daemon: `/run/api/agui`;
through a hub: `/rigs/<id>/run/api/agui`). One AG-UI run is one turn: it streams the clabe run — steps as
`STEP_STARTED`/`STEP_FINISHED`, messages as text messages, the run's state as `STATE_SNAPSHOT` — until the run needs
the user. An open prompt is a call of the frontend tool `clabe_prompt`, whose arguments are the prompt's `kind`,
`spec` and `answer_schema`; the turn then ends. The client answers by sending the next turn with a `tool` message for
that call, its content the answer as JSON. A rejected answer is said in a text message and the prompt is asked again.
