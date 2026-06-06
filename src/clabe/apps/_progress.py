import contextlib
import os
import threading
from typing import Any, Callable, Iterator, Optional, Union

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..logging_helper._stdlib import console as _default_console
from ._base import AsyncExecutor, Command, CommandResult, Executor

_Description = Union[str, Callable[[Command[Any]], str]]

#: Script-like file extensions used to surface a meaningful target in a command.
_SCRIPT_SUFFIXES = (".py", ".bonsai", ".ps1", ".sh", ".js", ".ts", ".rb", ".pl")


def _strip_exe(name: str) -> str:
    """Drop a trailing ``.exe`` for nicer display (e.g. ``bonsai.exe`` -> ``bonsai``)."""
    return name[:-4] if name.lower().endswith(".exe") else name


def _command_target(cmd: list[str]) -> Optional[str]:
    """Find the most informative target in a command's arguments.

    Prefers an explicit ``-m <module>`` target, otherwise the last script-like
    file (``.py``, ``.bonsai``, ...). Returns ``None`` when nothing stands out
    (e.g. a console-script entry point invoked via ``uv run``).
    """
    for i, tok in enumerate(cmd[1:], start=1):
        if tok == "-m" and i + 1 < len(cmd):
            return cmd[i + 1]
    scripts = [tok for tok in cmd[1:] if tok.lower().endswith(_SCRIPT_SUFFIXES)]
    if scripts:
        return os.path.basename(scripts[-1])
    return None


def _default_description(command: Command[Any]) -> str:
    """Derive a human-readable description from a command.

    Shows the program name, plus a script/module target when one can be
    identified (e.g. ``Running uv (analyze.py)``).
    """
    if not command.cmd:
        return "Running command"
    program = _strip_exe(os.path.basename(command.cmd[0]))
    target = _command_target(command.cmd)
    if target and target != program:
        return f"Running {program} ({target})"
    return f"Running {program}"


class ActivityIndicator:
    """
    Shared, thread-safe manager for a single live activity display.

    Owns a single ``rich.progress.Progress`` instance into which any number of
    concurrent executors register an "activity". Because a terminal can only
    host one live region at a time, every activity shares the same ``Progress``
    so concurrent spinners render as separate rows (each with its own spinner
    and elapsed-time counter) instead of fighting over the terminal.

    The display is reference-counted: it starts when the first activity is
    entered and stops (clearing itself) when the last activity exits. This
    works for both blocking synchronous executors running on worker threads and
    asynchronous executors driven by an event loop.

    The indicator shares its ``Console`` with the logging handler so that log
    lines emitted while the display is live render cleanly above the spinners
    rather than corrupting them.

    Attributes:
        enabled: Whether the display is active. Defaults to ``False`` on
            non-interactive consoles (e.g. when output is piped to a file or
            running in CI), making every activity a no-op.

    Example:
        ```python
        indicator = ActivityIndicator()
        with indicator.activity("Running my-task"):
            do_blocking_work()
        ```
    """

    def __init__(self, console: Optional[Console] = None, *, enabled: Optional[bool] = None) -> None:
        """
        Initialize the activity indicator.

        Args:
            console: Console to render on. Defaults to the shared console used
                by the logging handler so that logs and spinners coordinate.
            enabled: Force the display on or off. When ``None`` (default), the
                display is enabled only when the console is an interactive
                terminal.
        """
        self._console = console or _default_console
        self._enabled = self._console.is_terminal if enabled is None else enabled
        self._lock = threading.RLock()
        self._progress: Optional[Progress] = None
        self._active = 0

    @property
    def enabled(self) -> bool:
        """Whether the activity display is active."""
        return self._enabled

    def _build_progress(self) -> Progress:
        """Create the Progress instance used while activities are running."""
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
        )

    @contextlib.contextmanager
    def activity(self, description: str) -> Iterator[None]:
        """
        Display a spinner with elapsed time for the duration of the block.

        Safe to nest and to use concurrently from multiple threads or
        coroutines; all concurrent activities share a single display. When the
        indicator is disabled, this is a no-op context manager.

        Args:
            description: Text shown next to the spinner.

        Example:
            ```python
            with indicator.activity("Running bonsai"):
                executor.run(command)
            ```
        """
        if not self._enabled:
            yield
            return

        with self._lock:
            if self._active == 0:
                self._progress = self._build_progress()
                self._progress.start()
            assert self._progress is not None
            progress = self._progress
            task_id = progress.add_task(description, total=None)
            self._active += 1

        try:
            yield
        finally:
            with self._lock:
                progress.remove_task(task_id)
                self._active -= 1
                if self._active == 0:
                    progress.stop()
                    self._progress = None


_default_indicator: Optional[ActivityIndicator] = None
_default_indicator_lock = threading.Lock()


def get_activity_indicator() -> ActivityIndicator:
    """
    Return the process-wide shared :class:`ActivityIndicator`.

    Using a single shared indicator guarantees that concurrent executors
    cooperate on one live display rather than each trying to start its own
    (which the terminal cannot support).

    Returns:
        ActivityIndicator: The lazily-created shared indicator.
    """
    global _default_indicator
    with _default_indicator_lock:
        if _default_indicator is None:
            _default_indicator = ActivityIndicator()
        return _default_indicator


class ProgressExecutor:
    """
    Executor decorator that shows a live activity indicator during execution.

    Wraps any synchronous and/or asynchronous executor and, while delegating
    execution to it, displays a spinner and elapsed-time counter. Multiple
    ``ProgressExecutor`` instances active at the same time share a single
    display via the process-wide :func:`get_activity_indicator`, so concurrent
    executions (threaded or ``asyncio.gather``) each get their own row.

    The decorator forwards whichever interface the wrapped executor supports:
    ``run`` for synchronous executors and ``run_async`` for asynchronous ones.

    Example:
        ```python
        from clabe.apps._executors import LocalExecutor

        executor = ProgressExecutor(LocalExecutor())
        result = command.execute(executor)  # shows "Running <program>  0:00:03"

        # Custom label
        executor = ProgressExecutor(LocalExecutor(), description="Training model")

        # Wrapping an async executor
        executor = ProgressExecutor(AsyncLocalExecutor())
        result = await command.execute_async(executor)
        ```
    """

    def __init__(
        self,
        inner: Union[Executor, AsyncExecutor],
        *,
        description: Optional[_Description] = None,
        indicator: Optional[ActivityIndicator] = None,
    ) -> None:
        """
        Initialize the progress executor.

        Args:
            inner: The executor to delegate execution to. May implement the
                synchronous ``Executor`` protocol, the asynchronous
                ``AsyncExecutor`` protocol, or both.
            description: Text shown next to the spinner, or a callable taking
                the command and returning the text. Defaults to deriving the
                description from the command's program name.
            indicator: Activity indicator to render on. Defaults to the shared
                process-wide indicator.
        """
        self._inner = inner
        self._description = description
        self._indicator = indicator or get_activity_indicator()

    def _describe(self, command: Command[Any]) -> str:
        """Resolve the description text for a command."""
        if self._description is None:
            return _default_description(command)
        if callable(self._description):
            return self._description(command)
        return self._description

    def run(self, command: Command[Any]) -> CommandResult:
        """Execute synchronously via the wrapped executor with a live spinner."""
        if not isinstance(self._inner, Executor):
            raise TypeError(f"Wrapped executor {type(self._inner).__name__!r} does not support synchronous run().")
        with self._indicator.activity(self._describe(command)):
            return self._inner.run(command)

    async def run_async(self, command: Command[Any]) -> CommandResult:
        """Execute asynchronously via the wrapped executor with a live spinner."""
        if not isinstance(self._inner, AsyncExecutor):
            raise TypeError(f"Wrapped executor {type(self._inner).__name__!r} does not support run_async().")
        with self._indicator.activity(self._describe(command)):
            return await self._inner.run_async(command)
