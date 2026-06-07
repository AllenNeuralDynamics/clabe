# Frontends in CLABE

A **frontend** is the single boundary between CLABE and the person running an
experiment. It owns *all* user-facing presentation (status messages, headers,
live activity) and *all* interactive input (picking from a list, yes/no
questions, free text, numbers). It is deliberately decoupled from logging: a
logger is a durable diagnostic record, while a frontend is how a human is
informed and prompted. See [Logging](logging.md) for the other side of that
split.

Everything downstream — the launcher, the pickers, library `notify()` calls —
talks to the `Frontend` protocol and never knows which implementation is behind
it. Adding a new way to interact with CLABE means writing one class, not
touching the rest of the codebase.

## The ones we have

| Frontend | Class | What it is | When it's used |
|---|---|---|---|
| **Console** | `ConsoleFrontend` | A `rich`-styled, line-based console. On a real terminal it offers an arrow-key picker and a type-to-filter autocomplete; piped/CI it degrades to plain numbered prompts and `input()`. | `frontend = "console"`, or `auto` when not attached to a terminal |
| **TUI** | `TextualFrontend` | A persistent full-screen Textual app with four panes — **Session** (messages + answered prompts), **Processes** (live activity spinners), **Input** (the current prompt), **Logs**. | `frontend = "tui"`, or `auto` on a terminal (the default) |
| **Web** | *(not a frontend class)* | The **TUI served in a browser** via `textual-serve`. Not a separate implementation — it runs the `TextualFrontend` in a subprocess and proxies it to the browser. | `clabe serve …` (see below) |

## Choosing one

The launcher's `frontend` setting selects the backend:

```bash
clabe run my_experiment.py                      # auto (TUI on a terminal)
clabe run my_experiment.py --frontend tui       # force the TUI
clabe run my_experiment.py --frontend console   # force the rich console
```

| Value | Result |
|---|---|
| `auto` *(default)* | TUI when attached to a terminal, otherwise the console |
| `tui` | Always the Textual TUI |
| `console` | Always the rich console (also the safe choice for piping/CI) |

In code, build one with the factory:

```python
from clabe.ui import make_frontend, default_frontend

frontend = make_frontend("auto")   # or "tui" / "console"
frontend = default_frontend()      # the auto choice directly
```

A `Launcher` does this for you from `settings.frontend` and registers the result
process-wide so library code can reach it.

## Talking to the active frontend

High-level code holds a frontend and calls it directly (`self.frontend.notify(...)`,
`self.frontend.prompt_pick(...)`). Reusable library modules that should not take a
UI dependency use the process-wide helper instead:

```python
from clabe.ui import MessageLevel, notify

notify("Transferring data…", MessageLevel.INFO)
```

`notify()` is a **no-op when no launcher/frontend is active**, so it is safe to
call from library code used standalone. `MessageLevel` (`INFO`, `SUCCESS`,
`WARNING`, `ERROR`) expresses presentation intent; like the console, the Session
pane only renders at/above the current verbosity threshold (warnings and above
by default), while everything is still recorded to the transcript. See
[Logging](logging.md#logging-vs-talking-to-the-user) for `notify()` vs `logger`.

## TUI shortcuts and clickable paths

The Textual TUI shows a persistent header (with the CLABE version and, once
known, the running experiment) and a footer listing the active shortcuts:

| Key | Action |
|---|---|
| `Ctrl+C` | Exit the launcher |
| `Ctrl+S` | Save an SVG screenshot of the window |

`Ctrl+S` renders the whole window to an SVG in the OS temp directory and notes
the (clickable) path in the Session pane — terminal-independent, and a reliable
way to capture a run to share. Existing paths in the Session and Logs panes are
rendered as `file://` hyperlinks — `Ctrl`/`⌘`-click opens them in terminals that
support OSC 8 (e.g. Windows Terminal, iTerm2); elsewhere they render as ordinary
underlined text.

## Writing a new frontend

Subclass `FrontendBase` and implement the rendering/asking primitives; the base
class handles the transcript bridge, the validation/retry loop, and the
number-prompt loop for you:

```python
from clabe.ui import FrontendBase

class MyFrontend(FrontendBase):
    def _render(self, message, level): ...
    def _ask_text(self, request): ...
    def _ask_pick(self, request): ...
    def _ask_confirm(self, request): ...
    def _ask_autocomplete(self, request): ...
```

Optional hooks: `_render_header`, `_on_answer`, `set_min_level`, and `close`
(for frontends that own a resource, like the TUI's background app).

## Serving the TUI over the web

`clabe serve` runs the TUI in a browser via `textual-serve`, so a launcher can
be driven remotely without building a separate web UI:

```bash
clabe serve my_experiment.py --port 8080
```

- It binds **localhost only**. For remote access, forward the port over SSH
  rather than exposing it to the network:

  ```bash
  ssh -L 8080:localhost:8080 <rig-host>     # then open http://localhost:8080
  ```

- **Single session is enforced.** Each browser connection spawns its own
  launcher subprocess, so `clabe serve` passes `--single-session`: a stray second
  connection refuses to start (via an OS file lock) rather than fighting the live
  session over the rig.
- The "Session ended" screen has a **Finish** button (next to Restart) that
  shuts the server down and frees the port.
- `--open-browser` opens the page automatically once the server is ready (leave
  it off on headless/remote hosts).

In code, the same is available as `clabe.web.serve(command, …)`. Serving requires
the optional `web` extra:

```bash
pip install "aind-clabe[web]"
```
