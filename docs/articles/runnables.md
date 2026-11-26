# Runnables

Decorate an operation with [`runnable`][clabe.runnable.runnable] to get:

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


@runnable(notify_start=False, notify_success=False)  # notify only on failure
def check(self) -> bool: ...
```

`--verbose` and `--quiet` choose which notifications reach the screen. All notifications are logged.

## Overriding at the call site

```python
runnable(monitor.run, notify_start=False, notify_success=False)()  # quiet unless it fails
runnable(monitor.run, name="Disk check")()  # rename for one call
```

Wrapping a runnable again merges the flags, and it still shows a single spinner.

## Nesting

- When a runnable calls or awaits another runnable directly, only the outermost one shows a spinner and sends
  notifications. The inner ones still log.
- Tasks started with `asyncio.gather` or `asyncio.create_task` are independent. Each one gets its own spinner and
  notifications.

```python
@runnable(name="Experiment")
async def run_experiment(self) -> None:
    await asyncio.gather(
        runnable(self.behavior.run_async, name="Behavior")(),
        runnable(self.physiology.run_async, name="Physiology")(),
    )
```

## Telling the user

- On failure, raise an exception whose message says what went wrong. The user sees `"<name> failed: <message>"`.
- For any other information the user needs, call [`notify`][clabe.ui.notify].
- Do not do both for the same event, or the user sees it twice.

```python
raise RuntimeError(f"Need 10 GB free on {drive}")  # a specific message, instead of "constraints failed"
```

```python
from clabe.ui import MessageLevel, notify

notify("Found a single rig config; using it.", MessageLevel.WARNING)
```

## Inheritance

- A subclass that inherits the method without overriding it keeps the wrapper.
- An override is not wrapped. Re-apply `@runnable` to it, or call `super()`.
- For an abstract method, decorate the concrete implementation.
