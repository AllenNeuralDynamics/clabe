# Agents

Let an AI agent follow a run and answer its prompts, through MCP or AG-UI. The first valid answer wins, as with any client.

## MCP

`clabe mcp` serves a run server, a rig's [daemon](daemon.md) or a [hub](hub.md) to an MCP client over stdio:

```bash
uv add "aind-clabe[agents]"
clabe mcp --url http://rig-3:8070          # token: --token, else $DAEMON_CLABE_TOKEN, else $CLABE_TOKEN
```

In an MCP client's configuration:

```json
{"mcpServers": {"clabe": {"command": "uvx", "args": ["--from", "aind-clabe[agents]", "clabe", "mcp", "--url", "http://hub:8080"]}}}
```

| Tool | Does |
|---|---|
| `list_rigs` | The rigs it can act on (through a hub: status and what they wait for) |
| `get_run` | Steps, current step, open prompt and its JSON answer schema |
| `wait_for_prompt` | Waits until the run asks something (or ends) |
| `answer_prompt` | Answers the open prompt; returns the reason if rejected |
| `ask_the_user` | Asks the MCP client's user (elicitation), then submits |
| `go_back`, `retry_step`, `skip_step`, `abort_run` | The run's commands |

Through a hub, every tool takes the `rig` to act on.

## AG-UI

Every run speaks [AG-UI](https://docs.ag-ui.com):

| Via | Endpoint |
|---|---|
| Run server | `POST /api/agui` |
| Daemon | `POST /run/api/agui` |
| Hub | `POST /rigs/<id>/run/api/agui` |

- One AG-UI run is one turn: it streams steps (`STEP_STARTED`/`STEP_FINISHED`), messages, and state
  (`STATE_SNAPSHOT`) until the run needs the user.
- An open prompt is a call of the frontend tool `clabe_prompt` (arguments: `kind`, `spec`, `answer_schema`).
- Answer with a `tool` message for that call in the next turn, its content the answer as JSON.
- A rejected answer comes back as a text message, and the prompt is asked again.
