"""The run's Python log records (info and above), kept for the web UI's Log pane, like the TUI's Logs pane."""

import collections
import datetime
import logging
import threading
from collections.abc import Callable

from ._models import LogLine

#: How many records a buffer keeps (the oldest go first).
MAX_LINES = 2000


class LogBuffer(logging.Handler):
    """Keeps the latest log records as `LogLine`s, numbered from 1, and tells subscribers of each new one.

    Attach it with `attach` (to the root logger, as the TUI does) and remove it with `detach`.
    """

    def __init__(self, level: int = logging.INFO, maxlen: int = MAX_LINES) -> None:
        """Keeps records of ``level`` and above, at most ``maxlen`` of them."""
        super().__init__(level)
        self._lines: collections.deque[LogLine] = collections.deque(maxlen=maxlen)
        self._subscribers: list[Callable[[LogLine], None]] = []
        self._lock = threading.Lock()
        self._next_id = 1

    def emit(self, record: logging.LogRecord) -> None:
        """Keeps ``record`` and hands it to the subscribers."""
        try:
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{logging.Formatter().formatException(record.exc_info)}"
        except Exception:  # noqa: BLE001 -- formatting an arbitrary log record must never break logging
            return
        with self._lock:
            line = LogLine(
                id=self._next_id,
                ts=datetime.datetime.fromtimestamp(record.created, datetime.UTC),
                level=record.levelname.lower(),
                logger=record.name,
                message=message,
            )
            self._next_id += 1
            self._lines.append(line)
            subscribers = list(self._subscribers)
        for notify in subscribers:
            notify(line)

    def since(self, after: int) -> list[LogLine]:
        """The kept lines after id ``after``, oldest first."""
        with self._lock:
            return [line for line in self._lines if line.id > after]

    def subscribe(self, callback: Callable[[LogLine], None]) -> Callable[[], None]:
        """Calls ``callback`` with each new line (on the logging thread); returns the function that unsubscribes."""
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def attach(self, logger: logging.Logger | None = None) -> None:
        """Starts keeping the records of ``logger`` (the root logger by default)."""
        (logger or logging.getLogger()).addHandler(self)

    def detach(self, logger: logging.Logger | None = None) -> None:
        """Stops keeping the records of ``logger`` (the root logger by default)."""
        (logger or logging.getLogger()).removeHandler(self)
