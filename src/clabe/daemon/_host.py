"""The host role: list experiment repositories and run one experiment at a time in its repository's environment."""

import datetime
import json
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from ..server import free_port
from ..server._server import ANNOUNCE_ENV
from ..workflow import ExperimentListing
from ._settings import DaemonSettings, RepositoryConfig

logger = logging.getLogger(__name__)

#: Seconds to wait for a repository to list its experiments (a first `uv run` may install its environment).
LIST_TIMEOUT = 300.0


class HostError(Exception):
    """A request the host cannot honour; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class RepositoryInfo(BaseModel):
    """A configured repository."""

    name: str
    path: str
    exists: bool


class LaunchBody(BaseModel):
    """Which experiment to run."""

    repository: str
    file: str
    experiment: str | None = None


class RunInfo(BaseModel):
    """The run the host launched last."""

    repository: str
    file: str
    experiment: str | None
    status: Literal["starting", "running", "exited"]
    started_at: datetime.datetime
    pid: int
    port: int | None = None
    exit_code: int | None = None
    log_tail: list[str] = []


class HostInfo(BaseModel):
    """Who the host is and whether it is busy."""

    hostname: str
    clabe_version: str
    status: Literal["idle", "busy"]
    run: RunInfo | None


class _Launched:
    """A `clabe run` process started by the host."""

    def __init__(self, body: LaunchBody, process: subprocess.Popen, log: Path, announce: Path, token: str) -> None:
        self.body = body
        self.process = process
        self.log = log
        self.announce = announce
        self.token = token
        self.started_at = datetime.datetime.now(datetime.UTC)
        self.port: int | None = None

    def poll_port(self) -> int | None:
        """The run server's port, once it has announced it."""
        if self.port is None and self.announce.is_file():
            try:
                self.port = int(json.loads(self.announce.read_text(encoding="utf-8"))["port"])
            except (ValueError, KeyError, OSError):
                return None
        return self.port

    def info(self, tail: int = 40) -> RunInfo:
        """The run's state, with the end of its output."""
        exit_code = self.process.poll()
        port = self.poll_port()
        status = "exited" if exit_code is not None else "running" if port is not None else "starting"
        try:
            lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:]
        except OSError:
            lines = []
        return RunInfo(
            repository=self.body.repository,
            file=self.body.file,
            experiment=self.body.experiment,
            status=status,
            started_at=self.started_at,
            pid=self.process.pid,
            port=port,
            exit_code=exit_code,
            log_tail=lines,
        )


class Host:
    """Lists the configured repositories and runs one experiment at a time.

    A run is started as ``clabe run <file> --frontend headless --serve`` in the repository's own environment
    (``settings.command``), bound to localhost with a token only the host knows; the daemon proxies it.
    """

    def __init__(self, settings: DaemonSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._current: _Launched | None = None
        self._runs_dir = settings.runs_directory or Path(tempfile.gettempdir()) / "clabe-daemon"

    def repositories(self) -> list[RepositoryInfo]:
        """The configured repositories."""
        return [
            RepositoryInfo(name=r.display_name, path=str(r.path), exists=r.path.is_dir())
            for r in self.settings.repositories
        ]

    def repository(self, name: str) -> RepositoryConfig:
        """The repository called ``name``."""
        for repository in self.settings.repositories:
            if repository.display_name == name:
                return repository
        raise HostError(f"No repository named {name!r}.", status=404)

    def _command(self, repository: RepositoryConfig, *args: str) -> list[str]:
        return [part.replace("{repository}", str(repository.path)) for part in self.settings.command] + list(args)

    def experiments(self, name: str) -> ExperimentListing:
        """The experiments a repository offers, listed in its own environment (``clabe list --json``)."""
        repository = self.repository(name)
        if not repository.path.is_dir():
            raise HostError(f"{repository.path} does not exist.", status=404)
        command = self._command(repository, "list", repository.experiments, "--json")
        try:
            done = subprocess.run(
                command, cwd=repository.path, capture_output=True, text=True, timeout=LIST_TIMEOUT, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostError(f"Could not list the experiments of {name}: {exc}", status=502) from exc
        try:
            return ExperimentListing.model_validate_json(done.stdout)
        except ValidationError as exc:
            detail = (done.stderr or done.stdout).strip().splitlines()[-5:]
            raise HostError(f"`{' '.join(command)}` failed: {' / '.join(detail)}", status=502) from exc

    def launch(self, body: LaunchBody) -> RunInfo:
        """Starts ``body``'s experiment; refused while another run is going."""
        repository = self.repository(body.repository)
        with self._lock:
            if self._current is not None and self._current.process.poll() is None:
                raise HostError("A run is already going on this machine.", status=409)
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            log = self._runs_dir / f"{stamp}-{repository.display_name}.log"
            announce = self._runs_dir / f"{stamp}-{repository.display_name}.json"
            token = secrets.token_urlsafe(24)
            port = free_port("127.0.0.1", self.settings.port + 21)
            args = ["run", body.file, "--frontend", "headless", "--serve", "--host", "127.0.0.1"]
            args += ["--port", str(port), "--token", token, "--single-session"]
            if body.experiment:
                args += ["--experiment-name", body.experiment]
            command = self._command(repository, *args, *self.settings.run_arguments)
            env = {k: v for k, v in os.environ.items() if k != "CLABE_TOKEN"} | {
                ANNOUNCE_ENV: str(announce),
                "PYTHONUNBUFFERED": "1",
            }
            logger.info("Launching %s", " ".join(command))
            with log.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    command,
                    cwd=repository.path,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
                )
            self._current = _Launched(body, process, log, announce, token)
            return self._current.info()

    def current(self) -> _Launched | None:
        """The run launched last, running or not."""
        return self._current

    def stop(self, timeout: float = 10.0) -> RunInfo:
        """Stops the current run's process (prefer aborting it through its API: it cleans up)."""
        current = self._current
        if current is None or current.process.poll() is not None:
            raise HostError("No run is going.", status=409)
        current.process.terminate()
        deadline = time.monotonic() + timeout
        while current.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if current.process.poll() is None:
            current.process.kill()
        return current.info()

    def info(self) -> HostInfo:
        """Who the host is and what it runs."""
        import socket

        from .. import __version__

        current = self._current
        run = current.info(tail=0) if current is not None else None
        busy = run is not None and run.status != "exited"
        return HostInfo(
            hostname=socket.gethostname(), clabe_version=__version__, status="busy" if busy else "idle", run=run
        )
