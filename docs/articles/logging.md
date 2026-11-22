# Logging

clabe uses the standard `logging` module: log for the record, `notify()` for the user.

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Starting transfer to %s", destination)
logger.warning("Git repository is dirty: %s", changes)
```

- Always use a named logger, never the root logger or `print()`.
- Use `%`-style arguments, not f-strings: disabled levels then cost nothing.
- Attach a traceback (file only) with `exc_info=True`:

```python
try:
    do_something_risky()
except OSError as e:
    logger.error("Failed to do X: %s", e, exc_info=True)
    raise
```

## Levels

| Level | Use it for | On screen by default? |
| --- | --- | --- |
| `DEBUG` | Payloads, resolved paths, internal state | No (file only, with `--debug-mode`) |
| `INFO` | Normal milestones | No (with `--verbose`; always in the file) |
| `WARNING` | Unexpected but recoverable | Yes |
| `ERROR` | An operation failed | Yes |
| `CRITICAL` | Catastrophic failures | Yes |

## Where records go

| Sink | Level | Notes |
| --- | --- | --- |
| Console (`rich`) | `WARNING` | Follows the verbosity flags; muted while the TUI is open |
| `launcher.log` | `INFO` (`DEBUG` with `--debug-mode`) | Everything, UTC timestamps |
| TUI "Logs" pane | `INFO`+ | Textual frontend only |

## Verbosity

| Flag | Screen | Log file |
| --- | --- | --- |
| `--quiet` | `ERROR`+ | `INFO` |
| *(default)* | `WARNING`+ | `INFO` |
| `--verbose` | `INFO`+ | `INFO` |
| `--debug-mode` | `DEBUG`+ | `DEBUG` |

```bash
clabe run my_experiment.py --verbose
```

Or set the console level in code:

```python
import logging
from clabe.logging import set_console_level

set_console_level(logging.DEBUG)
```

## Telling the user

```python
from clabe.ui import MessageLevel, notify

logger.info("Starting robocopy transfer service.")  # the record
notify("Transferring data…", MessageLevel.INFO)  # what the user sees
```

- Levels: `INFO`, `SUCCESS`, `WARNING`, `ERROR`; the screen shows those at or above the verbosity threshold.
- With nothing to show it (no run, no launcher), `notify()` logs the message instead, so library code can call it.
- Every message, prompt and answer is recorded for you: in the run's `events.jsonl` (see [Run records](records.md)),
  or at debug level in the log file outside a run.

## Finding the log

After a run, the launcher copies its logs into the session:

```text
<data_directory>/<session_name>/Behavior/Logs/.launcher/launcher.log
```

- To debug a failed run, re-run with `--debug-mode` and open this file.
