"""Running the run API on a background thread."""

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .._extras import require_web
from ..workflow import Run
from ._logs import LogBuffer

if TYPE_CHECKING:
    import uvicorn

logger = logging.getLogger(__name__)

#: When set, a started server writes where it listens (JSON: ``port``, ``pid``) to this file, so the process that
#: launched the run (e.g. the daemon) finds it even after a fallback to another port.
ANNOUNCE_ENV = "CLABE_SERVER_ANNOUNCE"


def free_port(host: str, start: int, attempts: int = 50) -> int:
    """The first port from ``start`` on which ``host`` can listen."""
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"No free port in {start}..{start + attempts - 1} on {host}.")


class RunServer:
    """Serves a run over HTTP (API + web UI) on a background thread.

    Args:
        run: The run to serve.
        host: Interface to bind; ``127.0.0.1`` (default) keeps it local, ``0.0.0.0`` opens it to the LAN.
        port: Preferred port; the next free one is used when it is taken.
        token: When set, every API request must carry it.
    """

    def __init__(self, run: Run, *, host: str = "127.0.0.1", port: int = 8090, token: str | None = None) -> None:
        self.run = run
        self.host = host
        self.port = port
        self.token = token
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._logs: LogBuffer | None = None

    @property
    def url(self) -> str:
        """Where the run is served (with the token, when one is set).

        Open to the LAN (``0.0.0.0`` or ``::``), it names this computer, so the address also works from other PCs.
        """
        if self.host in ("0.0.0.0", "::"):
            import socket

            host = socket.gethostname()
        else:
            host = "localhost" if self.host == "127.0.0.1" else self.host
        suffix = f"/?token={self.token}" if self.token else "/"
        return f"http://{host}:{self.port}{suffix}"

    def start(self, timeout: float = 10.0) -> str:
        """Starts serving (on the next free port if needed) and returns the URL.

        Raises:
            ImportError: The web server (the ``frontend`` extra) is not installed.
        """
        require_web("Serving a run")
        import uvicorn

        from ._app import create_app

        self.port = free_port(self.host, self.port)
        self._logs = LogBuffer()  # the process' log records, for the web UI's Log pane (as the TUI's Logs pane)
        self._logs.attach()
        config = uvicorn.Config(
            create_app(self.run, token=self.token, logs=self._logs),
            host=self.host,
            port=self.port,
            log_level="warning",
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name=f"clabe-server-{self.port}", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self._detach_logs()
                raise RuntimeError(f"The run server did not start on {self.host}:{self.port}.")
            time.sleep(0.02)
        logger.info("Serving run %s at %s", self.run.id, self.url)
        if announce := os.environ.get(ANNOUNCE_ENV):
            Path(announce).write_text(json.dumps({"port": self.port, "pid": os.getpid()}), encoding="utf-8")
        return self.url

    def stop(self, timeout: float = 5.0) -> None:
        """Stops serving; open event streams are closed."""
        self._detach_logs()
        if self._server is None:
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._server = None
        self._thread = None

    def _detach_logs(self) -> None:
        """Stops keeping the process' log records."""
        if self._logs is not None:
            self._logs.detach()
            self._logs = None
