# Runnables

Decorate an operation with [`runnable`][clabe.runnable.runnable] to get, without extra code:

- a spinner while it runs,
- start / finish / failure notifications and log lines,
- an OpenTelemetry span (nested under the step's span in a run).

## Decorating

```python
from clabe.runnable import runnable


class ValveCalibration(Service):
    @runnable(name="Valve calibration")
    def run(self) -> float: ...


class MyApp:
    @runnable  # no name: reports the instance's class, e.g. "MyApp"
    def run(self) -> CommandResult: ...
```

- Return values and exceptions pass through unchanged.
- Works on sync and `async` methods and on plain functions.

## Options

| Flag | Default | Effect |
|---|---|---|
| `name` | class or function name | Display name |
| `notify` | — | Custom start message instead of `"<name>…"` |
| `show_activity` | `True` | Spinner while it runs |
| `notify_start` | `True` | `"<name>…"` |
| `notify_success` | `True` | `"<name> finished"` (with elapsed time under `--debug-mode`) |
| `notify_fail` | `True` | `"<name> failed: <exception>"` |

```python
@runnable(name="Transfer (watchdog)", notify="Transferring data…")
def transfer(self) -> None: ...


@runnable(notify_start=False, notify_success=False)  # only speak up on failure
def check(self) -> bool: ...
```

`--verbose` / `--quiet` choose which notifications reach the screen; all are logged.

## Overriding at the call site

```python
runnable(monitor.run, notify_start=False, notify_success=False)()  # quiet unless it fails
runnable(monitor.run, name="Disk check")()  # rename for one call
```

Rewrapping merges the flags; you never get two spinners.

## Nesting

- A runnable called directly (call or `await`) by another folds in: only the outermost shows a spinner and notifies.
  Inner ones still log.
- Tasks from `asyncio.gather` / `asyncio.create_task` are independent: each gets its own spinner and notifications.

```python
@runnable(name="Experiment")
async def run_experiment(self) -> None:
    await asyncio.gather(
        runnable(self.behavior.run_async, name="Behavior")(),
        runnable(self.physiology.run_async, name="Physiology")(),
    )
```

## Telling the user

- **Failure:** raise an exception whose message says what went wrong; it becomes `"<name> failed: <message>"`.
- **Anything else worth knowing:** call [`notify`][clabe.ui.notify].
- Don't do both for the same event, or the user sees it twice.

```python
raise RuntimeError(f"Need 10 GB free on {drive}")  # not "constraints failed"
```

```python
from clabe.ui import MessageLevel, notify

notify("Found a single rig config; using it.", MessageLevel.WARNING)
```

## Inheritance

- Inherited, not overridden: still wrapped.
- Overridden: plain again; re-apply `@runnable` (or call `super()`).
- Abstract methods: decorate the concrete implementation.
