# Talking to the user

Ask questions and show messages with `clabe.ui`; the same call works in the console, the TUI, the web UI and agents.
For the ideas behind it, see [Prompts and clients](../concepts/index.md#prompts-and-clients).

## Asking

```python
from clabe import ui

ui.notify("Transferring data…")
if ui.confirm("Start the session?", default=True):
    rig = ui.pick("Choose a rig:", ["rig-1", "rig-2"], allow_none=False)
    trials = ui.number("Trials", default=200, minimum=1)
    config = ui.form(SessionConfig)  # a pydantic model, field by field
    mode = ui.field(SessionConfig, "recording_mode")  # one field, with its type and constraints
```

- Answers come back validated and typed; `SecretStr` fields are masked in the records.
- Each helper wraps `ui.ask(request)` (`ui.TextRequest`, `ui.PickRequest`, ...).
- Also: `ui.text`, `ui.autocomplete`, `ui.path`, `ui.table`, `ui.acknowledge`, `ui.header`.

### Several prompts as one

```python
tuning = ui.group(
    "Tuning",
    note=ui.TextRequest(label="Operator note"),
    gain=ui.NumberRequest(label="Reward gain", minimum=0, maximum=1, step=0.05, widget="slider"),
)
tuning["gain"]  # a float in [0, 1]
```

Items: text, number, confirm, pick, autocomplete, path or field requests.

### Showing documents, images and pages

```python
ui.markdown("## Before you start\n\n1. Check the **water** line", title="Checklist")
ui.image("qc/frame_0001.png", title="Camera 1", caption="Last frame")  # a file or a URL
ui.html("qc/report.html", title="QC report")  # a file, a URL, or the HTML itself
ui.url("https://grafana.example.org/d/rig-3", title="Rig dashboard")
```

- Each waits until the user continues.
- Only the web UI shows images, HTML and URLs; the TUI and console log a warning naming them.

### When nothing can ask

- `ui.ask` and the helpers raise [`NoSessionError`][clabe.ui.NoSessionError].
- `ui.notify` / `ui.header` log the message instead.
- In tests: `with ui.bind(ClientSession(fake_client)):`.

- When going back, aborting or another client cancels the prompt, `ui.ask` raises
  [`PromptCancelledError`][clabe.ui.PromptCancelledError]: return or raise, don't ask again.

## Clients

```bash
clabe run my_experiment.py                      # auto: TUI on a terminal, console otherwise
clabe run my_experiment.py --frontend tui
clabe run my_experiment.py --frontend console
clabe run my_experiment.py --frontend headless  # no local screen: answer from the browser
```

| Client | Class | Notes |
|---|---|---|
| Console | [`ConsoleClient`][clabe.ui.ConsoleClient] | Rich, line-based; arrow-key pickers on a terminal, plain `input()` when piped |
| TUI | [`TextualClient`][clabe.ui.TextualClient] | Full-screen: Session, Processes, Input and Logs panes |
| Headless | [`HeadlessClient`][clabe.ui.HeadlessClient] | No screen; needs `--serve`; used by the [daemon](daemon.md) |
| Web UI | — | Served by the run; see [below](#serving-a-run-over-http) |
| Agents | — | MCP and AG-UI; see [Agents](agents.md) |

- Every prompt shows on every client; the first valid answer wins.
- In code: `ui.make_client("auto" | "tui" | "console" | "headless")`, or `Launcher(client=...)`.

### TUI shortcuts

| Key | Action |
|---|---|
| `Ctrl+C` | Abort the run (twice: force-quit) |
| `Ctrl+S` | Save an SVG screenshot |
| `F2` | Toggle the Logs pane |

### Writing a client

Implement the [`Client`][clabe.ui.Client] protocol; it never validates:

```python
class MyClient:
    def ask(self, request): ...  # show, block, return the raw answer; raise PromptCancelledError after cancel()
    def cancel(self): ...  # thread-safe
    def notify(self, message, level=ui.MessageLevel.INFO): ...
    def header(self, text): ...
    def close(self): ...
```

## The exit prompt

```python
wf = Workflow("vr-foraging", exit_prompt_timeout=60)  # or @experiment(..., exit_prompt_timeout=60)
```

| Value | Result |
|---|---|
| `None` (default) | Wait for Enter |
| `0` | Exit at once |
| `N` | Wait up to `N` seconds |

## Serving a run over HTTP

```bash
clabe run my_experiment.py                   # serves on http://localhost:8090 (or the next free port)
clabe run my_experiment.py --no-serve        # local client only
clabe run my_experiment.py --host 0.0.0.0 --token "$CLABE_TOKEN" --open-browser
```

- Binds localhost by default; for remote access use `ssh -L 8090:localhost:8090 <rig-host>` or bind `0.0.0.0` with
  a token.
- `--token` (or `$CLABE_TOKEN`) requires `Authorization: Bearer <token>` (or `?token=`); the printed URL carries it.
- The root page is the web UI: steps on the left (click one to go back), the open prompt on the right.
- [`RunClient`][clabe.server.RunClient] is a Python client of the API.

| Endpoint | Does |
|---|---|
| `GET /api/state` | Snapshot of the run |
| `GET /api/events` | Resumable Server-Sent Events stream |
| `POST /api/prompts/{id}/answer` | Answer a prompt |
| `POST /api/steps/{name}/back\|retry\|skip` | Step commands |
| `POST /api/abort` | Abort the run |
| `POST /api/fs/list` | Directory listing for path pickers |
| `/openapi.json`, `/docs` | The OpenAPI description |

## Migrating from the frontend API

| Old | New |
|---|---|
| `launcher.frontend.prompt_confirm(ConfirmRequest(label=...))` | `ui.confirm(...)` or `ui.ask(ConfirmRequest(...))` |
| `ui.prompt_pick(PickRequest(...))` (and every `ui.prompt_*`) | `ui.pick(...)` or `ui.ask(PickRequest(...))` |
| `ui.use_frontend(x)` / `ui.set_current_frontend(x)` | `ui.bind(ClientSession(client))` |
| `ConsoleFrontend` / `TextualFrontend` / `make_frontend` | `ConsoleClient` / `TextualClient` / `make_client` |
| `ui.NoFrontendError` | `ui.NoSessionError` |
| subclassing `FrontendBase` | implement `Client` |
