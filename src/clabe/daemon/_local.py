"""This machine's daemon, as a process on the same machine (``clabe run``, a terminal UI window) talks to it."""

import logging
import subprocess
import sys
import time
from typing import Any

import httpx

from .._process import NO_WINDOW
from ._host import HostInfo, RepositoryInfo, RunInfo

logger = logging.getLogger(__name__)


class DaemonRefused(Exception):
    """The daemon answered with an error; ``status`` is its HTTP status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


class LocalDaemon:
    """The daemon on this machine, from its settings (``DAEMON_CLABE_*``).

    Args:
        timeout: Seconds to wait for an answer.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        from ._settings import DaemonSettings

        settings = DaemonSettings()
        token = settings.token.get_secret_value() if settings.token else None
        self.base_url = f"http://127.0.0.1:{settings.port}/api/daemon"
        self.stop_grace = settings.stop_grace
        self._client = httpx.Client(
            timeout=timeout, headers={"Authorization": f"Bearer {token}"} if token else {}, trust_env=False
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        """Calls the API; its answer's JSON."""
        response = self._client.request(method, self.base_url + path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise DaemonRefused(response.status_code, str(detail))
        return response.json() if response.content else None

    def info(self) -> HostInfo:
        """Who the daemon is and what it runs."""
        return HostInfo.model_validate(self._call("GET", "/info"))

    def repositories(self) -> list[RepositoryInfo]:
        """The repositories it offers."""
        return [RepositoryInfo.model_validate(r) for r in self._call("GET", "/repositories")]

    def launch(self, body: dict[str, Any]) -> RunInfo:
        """Launches a run (see `LaunchBody`)."""
        return RunInfo.model_validate(self._call("POST", "/runs", json=body))

    def current(self) -> RunInfo | None:
        """The run it launched last, if any."""
        answer = self._call("GET", "/runs/current")
        return RunInfo.model_validate(answer) if answer else None

    def stop(self, *, wait: bool) -> None:
        """Stops the current run (with ``wait``, returns once it has ended)."""
        timeout = self.stop_grace + 15 if wait else self._client.timeout.read
        self._call("POST", "/runs/current/stop", params={"wait": wait}, timeout=timeout)

    def terminal(self) -> tuple[int, str]:
        """Where the current run's terminal UI is reached (port and token)."""
        answer = self._call("GET", "/runs/current/terminal")
        return int(answer["port"]), str(answer["token"])

    def answers(self) -> bool:
        """Whether the daemon answers."""
        try:
            self.info()
        except (httpx.HTTPError, DaemonRefused):
            return False
        return True


def start_task() -> bool:
    """Asks Windows to start the daemon's scheduled task; whether it agreed."""
    if sys.platform != "win32":
        return False
    from ._install import TASK_NAME

    try:
        done = subprocess.run(
            ["schtasks", "/Run", "/TN", TASK_NAME],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def reach(*, start: bool = True, wait: float = 10.0) -> LocalDaemon | None:
    """This machine's daemon, if it answers; starting its scheduled task first (and waiting ``wait`` seconds for
    it) when it does not and ``start``."""
    try:
        daemon = LocalDaemon()
    except Exception as exc:  # noqa: BLE001 -- unreadable settings: as if there were no daemon
        logger.debug("No daemon settings: %s", exc)
        return None
    if daemon.answers():
        return daemon
    if not start or not start_task():
        return None
    logger.info("Started the daemon's task; waiting for it to answer")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if daemon.answers():
            return daemon
    return None
