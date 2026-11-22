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
- A prompt answered before (you went back) is prefilled with that answer; **Forward** re-submits it.

### Options from a function

```python
subject = ui.autocomplete("Animal", options=lambda typed: subjects_service.search(typed))  # list[str]
```

- Called as the user types (debounced), by the run itself: the browser never calls your service.
- `strict=True` accepts a value only if the function returns it.

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

### Shortcuts

The same keys in the browser and the terminal UI (letters and digits only outside text fields). In the browser,
every action is also in the menu bar (Run, View, Session, Help), with its shortcut.

| Key | Action |
|---|---|
| `Enter` | Submit / continue (in a form: next field; the last submits) |
| `Ctrl+Enter`, `F5` | Submit from anywhere |
| `1`–`9`, `↑`/`↓` | Pick an option |
| `y` / `n` | Yes / No |
| `Alt+←` / `Alt+→` | Back to the previous step / Forward (re-submit your earlier answer) |
| `r` | Retry the failed step |
| `Esc` | Close a popup (never goes back or aborts) |
| `F2` | Show or hide the log |
| `Ctrl+K` | Browser: go to a step, run any command |
| `Ctrl+S`, `Ctrl+Shift+R` | Browser: export logs, report an issue |
| `?` | Browser: all shortcuts |
| `Ctrl+C` | Terminal: abort the run (twice: force-quit) |

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

## The end of a run

When the run ends, every client shows a closing screen: outcome, duration, the session folder, **Close session**,
**Export logs** and **Report an issue**. The first client to close wins (Enter in the terminal, the button in the
browser); the run server stays up until then. Then each browser tab closes itself after 5 s (**Keep it open** stops
it); a tab you opened by hand rather than through a link from clabe may not be allowed to close, and says so.

```python
wf = Workflow("vr-foraging", exit_prompt_timeout=60)  # or @experiment(..., exit_prompt_timeout=60)
```

| Value | Result |
|---|---|
| `None` (default) | Wait for a client to close |
| `0` | Exit at once |
| `N` | Count down `N` seconds (shown in every client), then exit |

- A headless served run (started by the daemon) waits up to `N` seconds for a browser to close it; with `None` or
  `0` it exits at once.

## Export logs and report an issue

- **Export logs** downloads one zip of the run so far: `run.json`, `events.jsonl`, `outputs.jsonl`,
  `launcher.log`, the daemon log's tail and `rig.json`.
- **Report an issue** saves the same zip with your comment and the session's details on the rig; the
  [hub](hub.md#issue-reports) collects it, then deletes it from the rig.

## Serving a run over HTTP

```bash
clabe run my_experiment.py                   # serves on http://localhost:8090 (or the next free port)
clabe run my_experiment.py --no-serve        # local client only
clabe run my_experiment.py --host 0.0.0.0 --token "$CLABE_TOKEN" --open-browser
```

- Binds localhost by default; for remote access use `ssh -L 8090:localhost:8090 <rig-host>` or bind `0.0.0.0` with
  a token.
- `--token` (or `$CLABE_TOKEN`) requires `Authorization: Bearer <token>` (or `?token=`); the printed URL carries it.
- The root page is the web UI: steps on the left (click one to go back), the prompt in the middle, the session and
  messages on the right; light and dark themes; works on a phone.
- [`RunClient`][clabe.server.RunClient] is a Python client of the API.

| Endpoint | Does |
|---|---|
| `GET /api/state` | Snapshot of the run |
| `GET /api/events` | Resumable Server-Sent Events stream |
| `POST /api/prompts/{id}/answer` | Answer a prompt |
| `POST /api/steps/{name}/back\|retry\|skip` | Step commands |
| `POST /api/back`, `POST /api/forward` | Back to the previous step; re-submit the open prompt's earlier answer |
| `GET /api/prompts/{id}/options?q=` | Options of an autocomplete backed by a function |
| `POST /api/close` | Close the finished session |
| `GET /api/export`, `POST /api/report` | Export logs (zip); report an issue (`{comment}`) |
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
