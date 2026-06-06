import contextlib
import threading
from typing import ContextManager, Iterator, Optional, Protocol

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ..logging_helper._stdlib import clabe_console as _default_console


class ActivitySink(Protocol):
    """An alternative destination for activity displays.

    When a sink is registered on an :class:`ActivityIndicator`, activities are
    rendered through the sink instead of the rich console. This lets a live TUI
    own the terminal and show progress *inside* itself, rather than having a
    console-based spinner corrupt the TUI's display.
    """

    def activity(self, description: str) -> ContextManager[None]:
        """Display activity for the duration of the returned context manager."""
        ...


class ActivityIndicator:
    """
    Shared, thread-safe manager for a single live activity display.

    Owns a single ``rich.progress.Progress`` instance into which any number of
    concurrent runnables register an "activity". Because a terminal can only
    host one live region at a time, every activity shares the same ``Progress``
    so concurrent spinners render as separate rows (each with its own spinner
    and elapsed-time counter) instead of fighting over the terminal.

    The display is reference-counted: it starts when the first activity is
    entered and stops (clearing itself) when the last activity exits. This
    works for both blocking synchronous code running on worker threads and
    asynchronous code driven by an event loop.

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
        self._sink: Optional[ActivitySink] = None

    @property
    def enabled(self) -> bool:
        """Whether the activity display is active."""
        return self._enabled

    def set_sink(self, sink: Optional[ActivitySink]) -> None:
        """
        Routes activities through ``sink`` (e.g. a TUI) instead of the console.

        Args:
            sink: The sink to render activities, or ``None`` to restore the
                default console rendering.
        """
        with self._lock:
            self._sink = sink

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
        """
        with self._lock:
            sink = self._sink

        if sink is not None:
            with sink.activity(description):
                yield
            return

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

    Using a single shared indicator guarantees that concurrent runnables
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
