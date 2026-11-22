# Logging

clabe uses the standard `logging` module. Use the logger for the record, and `notify()` for messages the user
should see.

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Starting transfer to %s", destination)
logger.warning("Git repository is dirty: %s", changes)
```

- Use a named logger. Do not use the root logger or `print()`.
- Pass `%`-style arguments instead of f-strings, so messages at disabled levels are never formatted.
- Add `exc_info=True` to attach a traceback (written to the file only):

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
| `launcher.log` | `INFO` (`DEBUG` with `--debug-mode`) | All records, with UTC timestamps |
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

- Levels are `INFO`, `SUCCESS`, `WARNING` and `ERROR`. The screen shows messages at or above the verbosity threshold.
- When there is no run or launcher to show the message, `notify()` logs it instead. Library code can therefore call
  `notify()`.
- clabe records every message, prompt and answer. Inside a run they go to the run's `events.jsonl` (see
  [Run records](records.md)). Outside a run they go to the log file at debug level.

## Finding the log

After a run, the launcher copies its logs into the session:

```text
<data_directory>/<session_name>/Behavior/Logs/.launcher/launcher.log
```

- To debug a failed run, re-run with `--debug-mode` and open this file.
