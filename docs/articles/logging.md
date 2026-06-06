# Logging in CLABE

This article explains how logging works in CLABE and how to use it in your own
launchers, apps and services.

## Mental model: logging is a *record*, not the UI

CLABE keeps two concerns separate:

- **Logging** — the durable, developer-facing **record** of what happened. It
  always captures everything and is the source of truth for debugging.
- **The Frontend (UI)** — what the **user** sees and answers (the banner,
  prompts, status messages, progress spinners).

Logging is a *sink*: your code writes records, and **where they end up** (a log
file, the console, the TUI "Logs" pane, a remote server) is decided by the
configured handlers — not by the call site. You therefore log freely at the
right level and let configuration decide what is shown.

> If you want something shown **to the user**, that is the Frontend's job — see
> the UI/Frontend documentation. Logging and the UI are deliberately decoupled.

## Getting a logger

Use the standard library. Every module creates a module-scoped logger:

```python
import logging

logger = logging.getLogger(__name__)
```

Because modules live under the `clabe` package, their loggers are named
`clabe.<module>` and inherit the configuration installed in `clabe/__init__.py`.
Never use the root logger or `print()` for diagnostics — always go through a
named logger.

## Choosing a level

| Level | Use it for | Shown by default? |
|---|---|---|
| `DEBUG` | Fine-grained detail for diagnosis (payloads, resolved paths, internal state) | No (file only with `--debug-mode`) |
| `INFO` | Normal milestones ("Executing command", "Manifest created") | No (shown with `--verbose`; always in the file) |
| `WARNING` | Unexpected-but-recoverable ("service not running, restarting", "deprecated version") | Yes |
| `ERROR` | An operation failed | Yes |
| `CRITICAL` | Rare, catastrophic failures | Yes |

```python
logger.debug("Resolved rig config: %s", path)
logger.info("Starting transfer to %s", destination)
logger.warning("Git repository is dirty: %s", changes)
logger.error("Failed to create directory %s: %s", directory, error)
```

Use **lazy `%`-style arguments** (`logger.info("x=%s", x)`), *not* f-strings.
With `%`-style, the string is only formatted if the level is actually enabled,
which keeps disabled `DEBUG` calls essentially free.

### Logging exceptions

Pass `exc_info=True` to attach the traceback to the record (it goes to the file,
not the user-facing console):

```python
try:
    do_something_risky()
except OSError as e:
    logger.error("Failed to do X: %s", e, exc_info=True)
    raise
```

## Where the records go (the sinks)

At import time, `clabe/__init__.py` configures the root logger with a console
handler. When a `Launcher` runs it adds the others:

| Sink | What | Default level | Notes |
|---|---|---|---|
| **Console** | A `rich` handler with severity highlighting | `WARNING` | Quieted/raised by the verbosity ladder; muted entirely while the TUI owns the terminal |
| **Log file** | `launcher.log` in the run's temp dir, UTC timestamps | `INFO` (`DEBUG` with `--debug-mode`) | The complete record; copied to `<session>/Behavior/Logs/.launcher/` at the end of a run |
| **TUI "Logs" pane** | Active only with the Textual frontend | `INFO`+ | Color-coded by level, local-time stamps |
| **Remote (AIBS)** | Optional socket handler to the AIBS log server | `ERROR` | Opt-in (see below) |

The key idea: **the file is exhaustive**, while the **console / TUI is filtered**
for humans. You can make the console quieter or noisier without ever losing
detail from the file.

## Controlling verbosity

A single ladder controls what is *shown* (the console and the user-facing
Session pane). The **log file always records full detail** regardless.

| Flag | Console / UI shows | Log file |
|---|---|---|
| `--quiet` | `ERROR`+ | `INFO` |
| *(default)* | `WARNING`+ | `INFO` |
| `--verbose` | `INFO`+ | `INFO` |
| `--debug-mode` | `DEBUG`+ | `DEBUG` |

```bash
clabe run my_experiment.py            # only warnings/errors on screen
clabe run my_experiment.py --verbose  # also show INFO
clabe run my_experiment.py --debug-mode
```

If you need to adjust the console level yourself, use the helper directly:

```python
import logging
from clabe import logging_helper

logging_helper.set_console_level(logging.INFO)
```

## Logging vs. talking to the user

A simple rule of thumb:

- **Diagnostics / the record** → `logger.*`. Lands in the file and the Logs pane.
- **A milestone the user cares about, or something they must act on** → surface
  it to the user *as well*.

High-level code (the launcher, pickers) holds a `frontend` and calls
`self.frontend.notify(...)`. Reusable library modules that should not take a UI
dependency can still surface a key event through the process-wide helper:

```python
import logging
from clabe.ui import MessageLevel, notify

logger = logging.getLogger(__name__)

logger.info("Starting robocopy transfer service.")   # the record (file / Logs pane)
notify("Transferring data…", MessageLevel.INFO)       # the user-facing summary (Session pane)
```

`notify()` is a **no-op when no launcher/frontend is active**, so it is safe to
call from library code used standalone. Anything sent through `notify()` is also
recorded to the log file (via the `clabe.transcript` logger), so the file stays
a complete transcript of what the user saw.

`MessageLevel` mirrors the logging levels for presentation intent:
`INFO`, `SUCCESS`, `WARNING`, `ERROR`. Like the console, the Session pane only
renders messages at/above the current verbosity threshold (warnings and above by
default), but the transcript records them all.

## The transcript

Everything the Frontend shows the user — and every prompt answer — is written to
a dedicated `clabe.transcript` logger. That logger reaches the **file** handler
(so `launcher.log` contains the user-facing narrative *and* the user's inputs)
but is filtered out of the **console** (the Frontend already rendered it), which
avoids double display.

This is why you don't need to manually log what you show the user: surfacing a
message or collecting an answer through the Frontend already records it.

## Sending logs to the AIBS log server (optional)

```python
import logging
from clabe.logging_helper.aibs import AibsLogServerHandlerSettings, add_handler

settings = AibsLogServerHandlerSettings(project_name="my_project", version="1.0.0")
add_handler(logging.getLogger(), settings)   # forwards ERROR+ records to the server
```

Or attach it to a launcher in one call:

```python
from clabe.logging_helper.aibs import AibsLogServerHandlerSettings, attach_to_launcher

attach_to_launcher(launcher, AibsLogServerHandlerSettings(project_name="my_project", version="1.0.0"))
```

The handler defaults to `ERROR`; set `level=logging.WARNING` on the settings to
forward more.

## Finding the log after a run

While a run is in progress, logs are written to a temporary directory. On
completion the launcher's `copy_logs()` copies them into the session directory:

```
<data_directory>/<session_name>/Behavior/Logs/.launcher/launcher.log
```

This file is the full UTC-timestamped record — diagnostics, the user-facing
transcript, and user input — for the entire run.

## Recipes

- **Add logging to a new module** — `logger = logging.getLogger(__name__)`, then
  log at the appropriate level with `%`-style args.
- **Make a message visible by default** — log it at `WARNING`/`ERROR`, or
  `notify()` it to the user.
- **Keep noisy detail off-screen but in the file** — use `DEBUG`.
- **Surface a library event to the user** — `clabe.ui.notify(...)` (and keep the
  `logger` call for the record).
- **Debug a failed run** — re-run with `--debug-mode`, or open
  `…/Behavior/Logs/.launcher/launcher.log`.
