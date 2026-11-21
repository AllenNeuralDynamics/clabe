"""Running the run API on a background thread."""

import logging
import socket
import threading
import time

import uvicorn

from ..workflow import Run
from ._app import create_app

logger = logging.getLogger(__name__)


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

    @property
    def url(self) -> str:
        """Where the run is served (with the token, when one is set)."""
        host = "localhost" if self.host in ("127.0.0.1", "0.0.0.0", "::") else self.host
        suffix = f"/?token={self.token}" if self.token else "/"
        return f"http://{host}:{self.port}{suffix}"

    def start(self, timeout: float = 10.0) -> str:
        """Starts serving (on the next free port if needed) and returns the URL."""
        self.port = free_port(self.host, self.port)
        config = uvicorn.Config(
            create_app(self.run, token=self.token),
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
                raise RuntimeError(f"The run server did not start on {self.host}:{self.port}.")
            time.sleep(0.02)
        logger.info("Serving run %s at %s", self.run.id, self.url)
        return self.url

    def stop(self, timeout: float = 5.0) -> None:
        """Stops serving; open event streams are closed."""
        if self._server is None:
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._server = None
        self._thread = None
