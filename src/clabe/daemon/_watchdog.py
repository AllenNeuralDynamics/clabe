"""The daemon's watchdog: a daemon whose web server stops answering ends itself, and its task starts a new one.

A thread inside the daemon asks the daemon's own page (``/api/daemon/info``) every `INTERVAL` seconds; any HTTP answer
(even "unauthorized") means the server works. After `LIMIT` seconds without one, it logs why, flushes the logs (the
log file and OpenTelemetry), and ends the process. The scheduled task's repeating trigger then starts a fresh daemon
(within its `_install.CHECK_INTERVAL`).
"""

import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

#: Seconds between two checks.
INTERVAL = 30.0

#: Seconds without an answer after which the daemon is ended.
LIMIT = 120.0


def flush_logs() -> None:
    """Flushes every log handler, and the OpenTelemetry logs and traces not sent yet (when it is set up)."""
    for handler in logging.getLogger().handlers:
        with contextlib.suppress(Exception):  # a handler that cannot flush must not keep the process from ending
            handler.flush()
    with contextlib.suppress(Exception):  # telemetry is optional
        from opentelemetry import _logs, trace

        for provider in (_logs.get_logger_provider(), trace.get_tracer_provider()):
            flush = getattr(provider, "force_flush", None)
            if flush is not None:
                flush(5_000)


def end_process(code: int) -> None:
    """Flushes the logs, then ends this process at once with ``code`` (a hung server would not stop by asking)."""
    flush_logs()
    os._exit(code)


class Watchdog:
    """Ends the daemon when ``url`` stops answering for ``limit`` seconds.

    Args:
        url: The daemon's own page, e.g. ``http://127.0.0.1:24070/api/daemon/info``.
        interval: Seconds between two checks.
        limit: Seconds without an answer after which ``end`` is called (counted from the start too, so a daemon that
            never serves is ended as well).
        end: Called with the exit code (1) to end the daemon; `end_process` by default.
    """

    def __init__(
        self,
        url: str,
        *,
        interval: float = INTERVAL,
        limit: float = LIMIT,
        end: Callable[[int], None] = end_process,
    ) -> None:
        self.url = url
        self.interval = interval
        self.limit = limit
        self.end = end
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="clabe-daemon-watchdog", daemon=True)

    def start(self) -> "Watchdog":
        """Starts watching, in a background thread."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stops watching (the daemon is shutting down)."""
        self._stop.set()
        self._thread.join(timeout=self.interval + 5)

    def _answers(self) -> bool:
        """Whether the daemon's server answers at all (any status)."""
        try:
            httpx.get(self.url, timeout=min(10.0, self.interval))
            return True
        except httpx.HTTPError:
            return False

    def _watch(self) -> None:
        """Checks every ``interval`` until stopped; ends the daemon after ``limit`` seconds without an answer."""
        last_answer = time.monotonic()
        while not self._stop.wait(self.interval):
            if self._answers():
                last_answer = time.monotonic()
                continue
            silent = time.monotonic() - last_answer
            if silent >= self.limit and not self._stop.is_set():
                logger.error(
                    "The daemon's server has not answered %s for %.0f s: ending the daemon (its task starts a new one).",
                    self.url,
                    silent,
                )
                self.end(1)
                return
            logger.warning("The daemon's server did not answer %s (for %.0f s)", self.url, silent)
