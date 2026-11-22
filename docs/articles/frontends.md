# Talking to the user: requests, sessions and clients

For the ideas behind this page, see [Prompts, sessions and clients](../concepts/prompts.md).

Everything CLABE shows to, or asks of, the person running an experiment goes through `clabe.ui`. Three pieces:

- **Requests** describe *what* to ask (`TextRequest`, `NumberRequest`, `PickRequest`, `FormRequest`, ...). Each one
  also describes itself as JSON for any client (`spec()`, `answer_schema()`) and checks an answer with its own rules
  (`validate_answer()`), so every client — console, TUI, the web UI or an agent — gets the same rules.
- A **session** is whatever can ask right now: the running **run** while an experiment executes, or the launcher's
  local client before and after it. Code never holds a frontend; it calls `ui.ask(...)` and the bound session
  answers.
- A **client** renders messages and collects raw answers (console, TUI, the web UI, agents). It never validates: whoever drives it — a
  run, or a `ClientSession` outside a run — validates and asks again with the reason.

Logging stays separate: a logger is a durable diagnostic record, `ui.notify` is how a human is informed. See
[Logging](logging.md) for that split. Inside a run every message, prompt and answer is also recorded in the run's
`events.jsonl`.

## Asking

```python
from clabe import ui

ui.notify("Transferring data…")
if ui.confirm("Start the session?", default=True):
    rig = ui.pick("Choose a rig:", ["rig-1", "rig-2"], allow_none=False)
    trials = ui.number("Trials", default=200, minimum=1)
    config = ui.form(SessionConfig)                     # a pydantic model, field by field
    mode = ui.field(SessionConfig, "recording_mode")    # one field of a model, with its type and constraints
```

Every helper builds the matching request and calls `ui.ask(request)`; you can build requests yourself and call
`ui.ask` directly. Answers come back validated and typed (a `Path`, an `Enum` member, a model instance, ...). A
`SecretStr` field is masked in the run's records.

### Several things at once

`ui.group` shows several prompts as one — e.g. a note and a slider — and returns their answers by name:

```python
tuning = ui.group(
    "Tuning",
    note=ui.TextRequest(label="Operator note"),
    gain=ui.NumberRequest(label="Reward gain", minimum=0, maximum=1, step=0.05, widget="slider"),
)
tuning["gain"]  # a float in [0, 1]
```

Items may be text, number, confirm, pick, autocomplete, path or field requests. A rejected answer names the item
(`gain: must be ≤ 1`). Clients without a slider show a number input with the range.

### Showing documents, images and pages

Four prompts show something and wait until the user continues (they return `None`):

```python
ui.markdown("## Before you start\n\n1. Check the **water** line", title="Checklist")
ui.image("qc/frame_0001.png", title="Camera 1", caption="Last frame")   # a local file or a URL
ui.html("qc/report.html", title="QC report")                            # a file, a URL, or the HTML itself
ui.url("https://grafana.example.org/d/rig-3", title="Rig dashboard")
```

| Prompt | Web UI | TUI | Console |
|---|---|---|---|
| `markdown` | rendered | rendered | rendered (Rich) |
| `image` | shown | warning naming the file | warning naming the file |
| `html` | sandboxed frame | warning naming the file | warning naming the file |
| `url` | frame + "open in a new tab" link | warning naming the URL | warning naming the URL |

A client that cannot show the media logs a warning naming the file or URL and still waits for the user to continue,
so a run served to the web is not answered behind the back of whoever is looking at the page. The web UI loads local
files through the run server, which serves only the file of that prompt; an HTML file is shown on its own (files it
links to next to it are not served). Raw HTML inside Markdown is not rendered; use `ui.html` for HTML.

### When nothing can ask

- `ui.ask` and the helpers raise `NoSessionError` — a prompt has to be answered, and failing loudly beats hanging on
  a rig.
- `ui.notify` / `ui.header` log the message instead, so library code stays safe to call standalone.

In tests, bind a session for the duration: `with ui.bind(ClientSession(fake_client)):` (or any object with `ask`,
`notify` and `header`).

## Clients

| Client | Class | What it is | When it's used |
| --- | --- | --- | --- |
| **Console** | `ConsoleClient` | A `rich`-styled, line-based console. On a real terminal it offers an arrow-key picker, a type-to-filter autocomplete and a path browser; piped/CI it degrades to plain prompts and `input()`. Forms are asked field by field. | `--frontend console`, or `auto` when not attached to a terminal |
| **TUI** | `TextualClient` | A persistent full-screen Textual app with four panes — **Session** (messages + answered prompts), **Processes** (live activity spinners), **Input** (the current prompt), **Logs**. | `--frontend tui`, or `auto` on a terminal (the default) |
| **Headless** | `HeadlessClient` | No screen: prompts wait until another client answers them, messages go to the log. | `--frontend headless` (needs `--serve`); runs started by the [daemon](daemon.md) |
| **Web UI** | — (a client of the run server) | The page served by the run: steps, the open prompt, messages, the log. | Whenever the run is served (the default for `clabe run`); see [below](#serving-a-run-over-http) |
| **Agents** | — (clients of the run server) | MCP and AG-UI adapters. | See [Agents](agents.md) |

```bash
clabe run my_experiment.py                      # auto (TUI on a terminal)
clabe run my_experiment.py --frontend tui       # force the TUI
clabe run my_experiment.py --frontend console   # force the rich console
clabe run my_experiment.py --frontend headless  # no local screen: answer from the browser
```

The local client and the remote ones share the run: every prompt shows on all of them and the first valid answer
wins.

In code: `ui.make_client("auto" | "tui" | "console" | "headless")`. A `Launcher` builds one from `settings.frontend` (or takes
`Launcher(client=...)`).

### Writing a client

Implement the `Client` protocol — five methods, no validation:

```python
class MyClient:
    def ask(self, request): ...        # show it, block, return the raw answer; raise PromptCancelledError after cancel()
    def cancel(self): ...              # thread-safe: abort the prompt being shown
    def notify(self, message, level=ui.MessageLevel.INFO): ...
    def header(self, text): ...
    def close(self): ...
```

`ask` receives only primitive requests (`request.prompt_kind` is one of text, number, confirm, pick, autocomplete,
path, form, table, acknowledge, group): field requests are resolved before they reach a client.

## Cancelling a prompt

A run cancels its open prompt when someone goes back, aborts, or answers from elsewhere; the local client's prompt
closes and the step's `ui.ask` raises `PromptCancelledError`. After `PromptCancelledError`, return or raise — don't
ask again. The console reads the keyboard key by key on a real terminal so its prompts can be cancelled; when stdin
or stdout is not a terminal it falls back to `input()`, which cannot be interrupted.

## The exit prompt

When an experiment finishes, the launcher waits at **Press Enter to exit...** so the last messages (and any error)
stay on screen. Each experiment sets that wait with `exit_prompt_timeout`:

| Value | Result |
| --- | --- |
| `None` (default) | Wait until Enter is pressed |
| `0` | Exit immediately, without prompting |
| `N` | Wait up to `N` seconds, then exit on its own |

```python
@experiment(name="photobleaching", exit_prompt_timeout=0)
async def photobleaching(launcher: Launcher) -> None: ...


@experiment(name="vr-foraging", exit_prompt_timeout=60)  # rigs must not stay blocked between sessions
async def vr_foraging(launcher: Launcher) -> None: ...
```

## Migrating from the frontend API

The frontend API was removed. Replace:

| Old | New |
| --- | --- |
| `launcher.frontend.prompt_confirm(ConfirmRequest(label=...))` | `ui.confirm(...)` or `ui.ask(ConfirmRequest(...))` |
| `ui.prompt_pick(PickRequest(...))` (and every `ui.prompt_*`) | `ui.pick(...)` or `ui.ask(PickRequest(...))` |
| `ui.use_frontend(x)` / `ui.set_current_frontend(x)` | `ui.bind(ClientSession(client))` |
| `ConsoleFrontend` / `TextualFrontend` / `make_frontend` | `ConsoleClient` / `TextualClient` / `make_client` |
| `ui.NoFrontendError` | `ui.NoSessionError` |
| subclassing `FrontendBase` | implement `Client` (see above) |

## TUI shortcuts and clickable paths

The Textual TUI shows a persistent header (with the CLABE version and, once known, the running experiment) and a
footer listing the active shortcuts:

| Key | Action |
| --- | --- |
| `Ctrl+C` | Abort the run (a second `Ctrl+C` force-quits) |
| `Ctrl+S` | Save an SVG screenshot of the window |
| `F2` | Toggle the Logs pane to reclaim vertical space |

`Ctrl+S` renders the whole window to an SVG in the OS temp directory and notes the (clickable) path in the Session
pane. Existing paths in the Session and Logs panes are rendered as `file://` hyperlinks — `Ctrl`/`⌘`-click opens them
in terminals that support OSC 8 (e.g. Windows Terminal, iTerm2).

## Serving a run over HTTP

With `--serve` (the default for `clabe run`), the launcher serves its run over HTTP while it executes. The local
frontend keeps working; any number of browsers or programs can follow the run and answer its prompts too — the
first valid answer wins and everyone sees it.

```bash
clabe run my_experiment.py                   # serves on http://localhost:8090 (or the next free port)
clabe run my_experiment.py --no-serve        # local frontend only
clabe run my_experiment.py --host 0.0.0.0 --token "$CLABE_TOKEN" --open-browser
```

- It binds **localhost only** by default. For remote access forward the port over SSH
  (`ssh -L 8090:localhost:8090 <rig-host>`), or bind `0.0.0.0` with a token.
- `--token` (or `$CLABE_TOKEN`) requires `Authorization: Bearer <token>` (or `?token=`) on every API call; the
  printed URL carries it.
- The API is described by OpenAPI at `/openapi.json` (browsable at `/docs`):
  `GET /api/state` (a snapshot), `GET /api/events` (a resumable Server-Sent Events stream of the run's events),
  `POST /api/prompts/{id}/answer`, `POST /api/steps/{name}/back|retry|skip`, `POST /api/abort`, and
  `POST /api/fs/list` (directory listings for remote path pickers).
- [`clabe.server.RunClient`][clabe.server.RunClient] is a small Python client of that API.

### The web UI

The page at the server's root is the web UI (Blazor WebAssembly, shipped in the wheel): the steps on the left —
click a finished one to go back to it — and the open prompt on the right, with recent messages, running
activities and the full event log. Every prompt kind is supported, including forms (rendered from the model's
JSON Schema), groups and sliders; path prompts browse the file system of the machine running the run. Answers are
validated by the run, so a rejected answer shows the same message as in the TUI.

The UI's source is in `ui-web/`. Its models are generated from the APIs' OpenAPI documents (`ui-web/openapi/`):
after changing an API, run `uv run python scripts/openapi.py` — a test fails until you do, and the UI then fails to
build wherever it used something that changed. To work on the UI, build it into the package with the .NET SDK
(`uv run python scripts/build_web.py`) — CI does this before building the wheel — and run
`tests/server/test_web_ui.py` (needs Playwright: `uv run --with playwright playwright install chromium`).

