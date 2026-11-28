# Run server reference

How a run is served over HTTP, its API, and its clients. [Prompts and frontends](../learn/prompts.md) explains them.

## Clients

| Client | Class | Notes |
|---|---|---|
| Console | [`ConsoleClient`][clabe.ui.ConsoleClient] | Rich, line-based; arrow-key pickers on a terminal, plain `input()` when piped |
| TUI | [`TextualClient`][clabe.ui.TextualClient] | Full-screen: Session, Processes, Input and Logs panes |
| Headless | [`HeadlessClient`][clabe.ui.HeadlessClient] | No screen; needs `--serve`; used by the [daemon](../rigs/set-up.md) |
| Web UI | — | Served by the run; see [Serving](#serving) |
| Agents | — | MCP and AG-UI; see [Agents](../rigs/agents.md) |

- Every prompt shows on every client. The run accepts the first valid answer.
- The TUI's bar under its header and the web UI's strip under its top bar show what the run is about:
  workflow, subject, experimenters, rig and host. A dash means the value is not known yet.
  - Subject and experimenters: from `launcher.register_session`, or from a step that returns a session.
  - Rig: the `aibs_comp_id` environment variable of the machine running the run, and nothing else (a workflow
    cannot set it).
- In code: `ui.make_client("auto" | "tui" | "console" | "headless")`, or `Launcher(client=...)`.

## Serving

```bash
clabe run my_experiment.py                   # serves on http://localhost:8090 (or the next free port)
clabe run my_experiment.py --no-serve        # local client only
clabe run my_experiment.py --host 0.0.0.0 --token "$CLABE_TOKEN" --open-browser
```

- The server binds localhost by default. For remote access, use `ssh -L 8090:localhost:8090 <rig-host>` or bind
  `0.0.0.0` with a token.
- `--token` (or `$CLABE_TOKEN`) requires `Authorization: Bearer <token>` (or `?token=`). The printed URL includes
  the token.
- In a browser, the page asks for the token (**Sign in**) and keeps it in a cookie for 30 days. A link with
  `?token=` signs in, and the address is then replaced by the same one without the token. The daemon and the hub
  work the same way.
- The root page is the web UI. It shows the steps on the left (click one to go back), the prompt in the middle, and
  the session and messages on the right. It has light and dark themes and works on a phone.
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

## Keyboard shortcuts

The browser and the terminal UI use the same keys. Letters and digits work only outside text fields. In the browser,
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
| `F3` | Terminal UI: report an issue |
| `Ctrl+K` | Browser: go to a step, run any command |
| `Ctrl+S`, `Ctrl+Shift+R` | Browser: export logs, report an issue |
| `?` | Browser: all shortcuts |
| `Ctrl+C` | Terminal: abort the run (twice: force-quit) |

## The closing screen

When the run ends, every client shows a closing screen: outcome, duration, the session folder, **Close session**,
**Export logs** and **Report an issue**. The session closes when the first client closes it (Enter in the terminal,
the button in the browser), and the run server stays up until then. Each browser tab then closes itself after 5 s,
unless you click **Keep it open**. The browser may not allow a tab you opened by hand (not through a link from clabe)
to close itself. In that case the tab says so.

## Writing a client

Implement the [`Client`][clabe.ui.Client] protocol. A client does not validate answers; the run does.

```python
class MyClient:
    def ask(self, request): ...  # show, block, return the raw answer; raise PromptCancelledError after cancel()
    def cancel(self): ...  # thread-safe
    def notify(self, message, level=ui.MessageLevel.INFO): ...
    def header(self, text): ...
    def close(self): ...
```
