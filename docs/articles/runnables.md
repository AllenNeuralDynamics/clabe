# Runnables

CLABE wraps its "do the thing" operations — running an app, transferring data,
checking resources — in a single decorator, `runnable`. Decorating a method
gives it, for free:

- a live **activity spinner** while it runs (a no-op on non-interactive
  consoles),
- structured **log lines** on start/finish/failure,
- user-facing **notifications** to whoever is asking (the run, or the launcher's client), and
- an **OpenTelemetry** span (nested under the step's span inside a run).

Instead of hand-writing `notify(...)` calls and start/finish logging inside each
operation, decorate the method and let the shared lifecycle handle it.

## Decorating a method

```python
from clabe.runnable import runnable


class ResourceMonitor(Service):
    @runnable(name="Resource monitor")
    def run(self) -> bool: ...
```

The decorator forwards the return value unchanged and re-raises any exception
unchanged — it only *wraps* the call. It works on `async def` methods too
(detected automatically) and on plain functions.

If you omit `name`, it is derived at call time from the instance's class (so
subclasses report themselves):

```python
class _DefaultExecutorMixin:
    @runnable  # BonsaiApp().run() reports "BonsaiApp"
    def run(self, executor_kwargs=None) -> CommandResult:
        return self.command.execute(LocalExecutor(**(executor_kwargs or {})))
```

## What it reports

By default a runnable shows a spinner, notifies when it starts (`"<name>…"`), when it finishes and when it fails,
and logs all three. Turn each off with a flag, or set the start message:

| Flag | Default | Effect |
| --- | --- | --- |
| `show_activity` | `True` | Spinner (an activity) while it runs |
| `notify_start` | `True` | `"<name>…"`, or the `notify=` message |
| `notify_success` | `True` | `"<name> finished"` (with the elapsed time under `--debug-mode`) |
| `notify_fail` | `True` | `"<name> failed: <exception>"` |
| `notify` | — | Custom start message |

```python
@runnable(name="Transfer (watchdog)", notify="Transferring data…")
def transfer(self) -> None: ...


@runnable(notify_start=False, notify_success=False)   # only speak up on failure
def check(self) -> bool: ...
```

Which notifications reach the screen is then the user's choice: the UI shows messages at or above its verbosity
(`--verbose` shows everything, `--quiet` only errors); everything is logged and recorded regardless.

## Overriding at the call site

`runnable` can also rewrap an existing callable to override its flags for one
call — useful for a step quieting or renaming an app's run without editing the
app:

```python
runnable(monitor.run, notify_start=False, notify_success=False)()  # quiet unless it fails
runnable(monitor.run, name="Disk check")()  # rename for one run
```

Rewrapping an already-decorated method **merges** over its baked-in spec rather
than nesting, so you never get two spinners or duplicate notifications.

## Nesting

When one runnable **directly calls** another (a plain call or `await`), the
inner one folds into the outer: only the **outermost** runnable shows a spinner
and emits start/success/failure notifications. Inner runnables still log. This
means an inner failure that the outer handles stays quiet, while a failure that
propagates all the way out is announced exactly once.

```python
@runnable(name="Transfer (robocopy)", notify="Transferring data…")
def transfer(self) -> None:
    self.run()  # the mixin's run() is also a runnable, but folds in here
```

Tasks spawned concurrently with `asyncio.gather()` or `asyncio.create_task()`
are **not** treated as nested — each is an independent lifecycle with its own
spinner and notifications. This is intentional: concurrent tasks are peers, not
children.

```python
@runnable(name="Experiment")
async def run_experiment(self) -> None:
    # Both subtasks get their own spinner and failure notification.
    await asyncio.gather(
        runnable(self.behavior.run_async, name="Behavior")(),
        runnable(self.physiology.run_async, name="Physiology")(),
    )
```

## Surfacing information to the user

There are two channels, and they should not be crossed:

- **Failures → the exception message.** On failure the lifecycle calls
  `notify(f"{name} failed: {exc}", ERROR)`, so the user-facing error is only as
  good as the exception's message. Don't add a side-channel — raise an exception
  that *says what went wrong*. This also composes with nesting: because only the
  outermost runnable notifies on failure, a detailed message is what survives to
  the top and is shown exactly once.

  ```python
  # Vague — the user sees "Resource monitor failed: constraints failed"
  raise RuntimeError("Resource monitor constraints failed.")

  # Actionable — the user sees "Resource monitor failed: Need 10GB free on C:\\"
  raise RuntimeError(constraint.on_fail())
  ```

- **Important non-failure info → an explicit `notify()`.** Warnings, partial
  outcomes, or notable state aren't lifecycle events, so call
  [`notify`][clabe.ui.notify] directly:

  ```python
  from clabe.ui import MessageLevel, notify

  notify("Found a single rig config; using it.", MessageLevel.WARNING)
  ```

Avoid doing **both** for the same event — don't `notify(detail, ERROR)` *and*
`raise` the same thing, or the user sees it twice (once immediately, once as the
runnable unwinds). For a failure, prefer the exception.

## Inheritance

Decoration follows normal attribute lookup:

- **Inherited without overriding** — works; the subclass uses the base's wrapped
  method.
- **Overridden** — the override is a plain method again; re-apply `@runnable` (or
  call `super().<method>()`) if you want it tracked.
- **Abstract methods** — decorate the concrete implementation, not the
  `@abstractmethod` (a wrapped abstract method is always replaced by the
  override anyway).
