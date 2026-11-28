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
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .._process import NO_WINDOW, Job, popen_in_job
from ..server import free_port
from ..server._server import ANNOUNCE_ENV
from ..workflow import ExperimentListing
from . import _terminal
from ._settings import MAIN_FILE, DaemonSettings, RepositoryConfig

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


#: What a launched run shows on the rig's screen, besides being served to browsers: nothing, the terminal UI in a
#: console window, or the run's page in the rig's default browser.
Frontend = Literal["none", "tui", "browser"]


class LaunchBody(BaseModel):
    """Which experiment to run."""

    repository: str
    file: str = MAIN_FILE
    experiment: str | None = None
    frontend: Frontend = Field(
        default="none", description="What the run shows on the rig: nothing, the terminal UI (tui), or the browser"
    )
    window: bool = Field(
        default=True,
        description="With the terminal UI: open a window on the rig showing it (false: whoever launched shows it)",
    )
    from_rig: bool = Field(default=False, description="Launched at the rig (its desktop icon) rather than remotely")
    arguments: list[str] = Field(default_factory=list, description="More `clabe run` arguments")


class AttachBody(BaseModel):
    """A run started on this machine outside the daemon (e.g. from a terminal), announcing itself."""

    port: int
    token: str | None = None
    pid: int
    repository: str
    file: str = MAIN_FILE
    experiment: str | None = None


class RunInfo(BaseModel):
    """The run the host launched last."""

    repository: str
    file: str
    experiment: str | None
    status: Literal["starting", "running", "exited"]
    started_at: datetime.datetime
    pid: int
    origin: Literal["daemon", "terminal"] = "daemon"
    from_rig: bool = Field(default=False, description="Launched at the rig rather than remotely")
    terminal: bool = Field(default=False, description="It shows the terminal UI, which a window on the rig can show")
    port: int | None = None
    exit_code: int | None = None
    log_tail: list[str] = []


#: The environment variable that names an Allen Institute rig (e.g. ``"RIG_12"``), set machine-wide on rig PCs.
RIG_ID_ENV = "AIBS_RIG_ID"


def rig_id() -> str | None:
    """This rig's name at the Allen Institute (``AIBS_RIG_ID``), or ``None`` when it is not set."""
    return os.environ.get(RIG_ID_ENV, "").strip() or None


def ip_address() -> str | None:
    """This machine's IPv4 address (that of the interface with the default route), or ``None`` when it has none."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # UDP: sends nothing, only picks the outgoing interface
            return probe.getsockname()[0]
    except OSError:
        pass
    try:
        address = socket.gethostbyname(socket.gethostname())
    except OSError:
        return None
    return None if address.startswith("127.") else address


class HostInfo(BaseModel):
    """Who the host is and whether it is busy."""

    hostname: str
    rig_id: str | None = Field(default=None, description="The rig's name at the Allen Institute (AIBS_RIG_ID), if set")
    ip: str | None = Field(default=None, description="This machine's IPv4 address, if it has one")
    clabe_version: str
    terminals: bool = Field(
        default=False, description="Runs launched here can show the terminal UI in windows that close and reopen"
    )
    status: Literal["idle", "busy", "leased"]
    run: RunInfo | None
    leased_by: str | None = None
    reports_waiting: int = Field(default=0, description="Issue reports on this machine, not collected by the hub yet")


class _Launched:
    """A `clabe run` process started by the host (directly, or under a terminal host), or found again after a
    daemon restart (then only known by its process id)."""

    def __init__(
        self,
        body: LaunchBody,
        process: subprocess.Popen | None,
        log: Path,
        announce: Path,
        token: str,
        *,
        pid: int | None = None,
        job: Job | None = None,
        terminal: Path | None = None,
        terminal_token: str | None = None,
        started_at: datetime.datetime | None = None,
    ) -> None:
        self.body = body
        self.process = process
        self.pid = process.pid if process is not None else int(pid or 0)
        self.log = log
        self.announce = announce
        self.token = token
        self.job = job
        self.terminal = terminal
        self.terminal_token = terminal_token
        self.started_at = started_at or datetime.datetime.now(datetime.UTC)
        self.port: int | None = None

    def alive(self) -> bool:
        """Whether the run's process is still going."""
        return self.process.poll() is None if self.process is not None else pid_alive(self.pid)

    def exit_code(self) -> int | None:
        """The run's exit code, once it has ended and when it is known."""
        return self.process.poll() if self.process is not None else None

    def poll_port(self) -> int | None:
        """The run server's port, once it has announced it."""
        if self.port is None and self.announce.is_file():
            try:
                self.port = int(json.loads(self.announce.read_text(encoding="utf-8"))["port"])
            except (ValueError, KeyError, OSError):
                return None
        return self.port

    def terminal_address(self) -> tuple[int, str] | None:
        """Where windows reach the run's terminal UI (port and token), once its host listens."""
        if self.terminal is None or self.terminal_token is None or not self.terminal.is_file():
            return None
        try:
            return int(json.loads(self.terminal.read_text(encoding="utf-8"))["port"]), self.terminal_token
        except (ValueError, KeyError, OSError):
            return None

    def kill(self) -> None:
        """Ends the run's process and every process it started (but those started to outlive it)."""
        if self.job is not None:
            self.job.terminate()
        else:  # found again after a restart: its job is gone, its processes are not
            for pid in (_announced_pid(self.announce), self.pid):
                if pid:
                    terminate_pid(pid)
        if self.process is not None and self.process.poll() is None:
            self.process.kill()

    def info(self, tail: int = 40) -> RunInfo:
        """The run's state, with the end of its output."""
        alive = self.alive()
        port = self.poll_port()
        status = "exited" if not alive else "running" if port is not None else "starting"
        try:
            lines = self.log.read_text(encoding="utf-8", errors="replace").splitlines()[-tail:] if tail else []
        except OSError:
            lines = []
        return RunInfo(
            repository=self.body.repository,
            file=self.body.file,
            experiment=self.body.experiment,
            status=status,
            started_at=self.started_at,
            pid=self.pid,
            from_rig=self.body.from_rig,
            terminal=self.terminal is not None,
            port=port,
            exit_code=None if alive else self.exit_code(),
            log_tail=lines,
        )

    def record(self) -> dict:
        """What a restarted daemon needs to find this run again."""
        return {
            "body": self.body.model_dump(),
            "pid": self.pid,
            "log": str(self.log),
            "announce": str(self.announce),
            "token": self.token,
            "terminal": str(self.terminal) if self.terminal else None,
            "terminal_token": self.terminal_token,
            "started_at": self.started_at.isoformat(),
        }

    @classmethod
    def from_record(cls, record: dict) -> "_Launched":
        """A run a previous daemon launched, from its `record`."""
        return cls(
            LaunchBody.model_validate(record["body"]),
            None,
            Path(record["log"]),
            Path(record["announce"]),
            record["token"],
            pid=int(record["pid"]),
            terminal=Path(record["terminal"]) if record.get("terminal") else None,
            terminal_token=record.get("terminal_token"),
            started_at=datetime.datetime.fromisoformat(record["started_at"]),
        )


def _announced_pid(announce: Path) -> int | None:
    """The process id a run server announced (the ``clabe run`` process itself)."""
    try:
        return int(json.loads(announce.read_text(encoding="utf-8"))["pid"])
    except (ValueError, KeyError, OSError):
        return None


class _Attached:
    """A run started outside the daemon that announced itself; followed until its process ends or it withdraws."""

    def __init__(self, body: AttachBody) -> None:
        self.body = body
        self.token = body.token
        self.port: int | None = body.port
        self.started_at = datetime.datetime.now(datetime.UTC)

    def alive(self) -> bool:
        """Whether the run's process is still going (and it has not withdrawn)."""
        return self.port is not None and pid_alive(self.body.pid)

    def poll_port(self) -> int | None:
        """The run server's port."""
        return self.port

    def kill(self) -> None:
        """Ends the run's process (it takes the processes it started with it)."""
        terminate_pid(self.body.pid)

    def info(self, tail: int = 40) -> RunInfo:
        """The run's state (its output is in its own terminal)."""
        return RunInfo(
            repository=self.body.repository,
            file=self.body.file,
            experiment=self.body.experiment,
            status="running" if self.alive() else "exited",
            started_at=self.started_at,
            pid=self.body.pid,
            origin="terminal",
            from_rig=True,
            port=self.port,
        )


def terminate_pid(pid: int) -> None:
    """Ends process ``pid`` (a ``clabe run`` process takes the processes it started with it)."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return
    import signal

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    """Whether process ``pid`` is running (without signalling it: on Windows, ``os.kill`` would end it)."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_arguments(body: LaunchBody, port: int, token: str) -> list[str]:
    """The ``clabe run`` arguments of a launched run: served on ``port`` with ``token``, showing ``body.frontend``."""
    frontend = "tui" if body.frontend == "tui" else "headless"
    args = ["run", body.file, "--frontend", frontend, "--serve", "--host", "127.0.0.1"]
    args += ["--port", str(port), f"--token={token}", "--single-session"]  # "=": a token may start with "-"
    if body.frontend == "browser":
        args.append("--open-browser")
    if body.experiment:
        args += ["--experiment-name", body.experiment]
    return args + body.arguments


def _python() -> str:
    """The Python of this environment with a console (the daemon itself may run windowless, as pythonw)."""
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    return str(console if executable.name.lower() == "pythonw.exe" and console.is_file() else executable)


def open_window() -> None:
    """Opens a window on this machine's screen showing the current run's terminal UI (`clabe daemon view`)."""
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    try:
        subprocess.Popen([_python(), "-m", "clabe", "daemon", "view"], creationflags=flags | 0x01000000)
    except PermissionError:  # not allowed out of the daemon's job: fine, the daemon's job never ends its children
        subprocess.Popen([_python(), "-m", "clabe", "daemon", "view"], creationflags=flags)


class Host:
    """Lists the configured repositories and runs one experiment at a time.

    A run is started as ``clabe run <file> --serve`` in the repository's own environment (``settings.command``),
    bound to localhost with a token only the host knows; the daemon proxies it. It runs headless, or, showing the
    terminal UI, under a terminal host (see `clabe.daemon._terminal`) that windows on the rig show and leave. Each
    run is in a job, so stopping it ends what it started. A restarted daemon finds the run it had launched again.
    """

    def __init__(self, settings: DaemonSettings, *, leased_by: Callable[[], str | None] = lambda: None) -> None:
        self.settings = settings
        self._leased_by = leased_by
        self._lock = threading.Lock()
        self._current: _Launched | _Attached | None = None
        self._runs_dir = settings.runs_directory or Path(tempfile.gettempdir()) / "clabe-daemon"
        self._current = self._find_running()

    def _find_running(self) -> "_Launched | None":
        """The run a previous daemon launched, if it is still going (its record is in the runs directory)."""
        try:
            records = sorted(self._runs_dir.glob("*.run.json"), reverse=True)
        except OSError:
            return None
        for path in records[:1]:
            try:
                launched = _Launched.from_record(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError, OSError, ValidationError):
                continue
            if launched.alive():
                logger.info("Found the run of %s (pid %s) again", launched.body.repository, launched.pid)
                return launched
        return None

    def repositories(self) -> list[RepositoryInfo]:
        """The configured repositories."""
        return [
            RepositoryInfo(name=r.display_name, path=str(r.path), exists=r.path.is_dir())
            for r in self.settings.find_repositories()
        ]

    def repository(self, name: str) -> RepositoryConfig:
        """The repository called ``name``."""
        for repository in self.settings.find_repositories():
            if repository.display_name == name:
                return repository
        raise HostError(f"No repository named {name!r}.", status=404)

    def _command(self, repository: RepositoryConfig, *args: str) -> list[str]:
        """``clabe <args>`` in ``repository``'s environment (``settings.command``)."""
        return [part.replace("{repository}", str(repository.path)) for part in self.settings.command] + list(args)

    def experiments(self, name: str) -> ExperimentListing:
        """The experiments a repository offers, listed in its own environment (``clabe list --json``)."""
        repository = self.repository(name)
        if not repository.path.is_dir():
            raise HostError(f"{repository.path} does not exist.", status=404)
        command = self._command(repository, "list", MAIN_FILE, "--json")
        try:
            done = subprocess.run(
                command,
                cwd=repository.path,
                capture_output=True,
                text=True,
                timeout=LIST_TIMEOUT,
                check=False,
                creationflags=NO_WINDOW,
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
        if body.frontend == "tui" and not _terminal.available():
            raise HostError("The terminal UI can only be shown on a Windows rig (with pywinpty).", status=400)
        repository = self.repository(body.repository)
        with self._lock:
            if self._current is not None and self._current.alive():
                raise HostError("A run is already going on this machine.", status=409)
            if (owner := self._leased_by()) is not None:
                raise HostError(f"This machine is in use by {owner}.", status=423)
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
            log = self._runs_dir / f"{stamp}-{repository.display_name}.log"
            announce = self._runs_dir / f"{stamp}-{repository.display_name}.json"
            token = secrets.token_urlsafe(24)
            port = free_port("127.0.0.1", self.settings.port + 21)
            args = run_arguments(body, port, token)
            command = self._command(repository, *args, *self.settings.run_arguments)
            hidden = ("CLABE_TOKEN", "DAEMON_CLABE_TOKEN")  # the run gets its own token, not the daemon's
            env = {k: v for k, v in os.environ.items() if k not in hidden} | {
                ANNOUNCE_ENV: str(announce),
                "PYTHONUNBUFFERED": "1",
            }
            logger.info("Launching %s", " ".join(command))
            job = _new_job()
            terminal = terminal_token = None
            if body.frontend == "tui":  # in a hidden console, under a host that windows connect to
                terminal = self._runs_dir / f"{stamp}-{repository.display_name}.terminal.json"
                terminal_token = secrets.token_urlsafe(24)
                spec = self._runs_dir / f"{stamp}-{repository.display_name}.terminal-spec.json"
                spec.write_text(
                    json.dumps(
                        {
                            "argv": command,
                            "cwd": str(repository.path),
                            "token": terminal_token,
                            "address_file": str(terminal),
                        }
                    ),
                    encoding="utf-8",
                )
                command = [_python(), "-m", "clabe.daemon._terminal", str(spec)]
            with log.open("w", encoding="utf-8") as output:
                if terminal is not None:
                    output.write("Started with the terminal UI: what it shows is in its window.\n")
                    output.flush()
                process = popen_in_job(
                    job,
                    command,
                    cwd=repository.path,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0) | NO_WINDOW,
                )
            launched = _Launched(
                body, process, log, announce, token, job=job, terminal=terminal, terminal_token=terminal_token
            )
            record = self._runs_dir / f"{stamp}-{repository.display_name}.run.json"
            record.write_text(json.dumps(launched.record()), encoding="utf-8")
            self._current = launched
            if terminal is not None and body.window:
                open_window()
            return launched.info()

    def current(self) -> "_Launched | _Attached | None":
        """The run launched (or attached) last, running or not."""
        return self._current

    def attach(self, body: AttachBody) -> RunInfo:
        """Follows a run started outside the daemon on this machine; refused while another run is going."""
        with self._lock:
            current = self._current
            again = isinstance(current, _Attached) and current.body.pid == body.pid
            if current is not None and current.alive() and not again:
                raise HostError("A run is already going on this machine.", status=409)
            if (owner := self._leased_by()) is not None:
                raise HostError(f"This machine is in use by {owner}.", status=423)
            self._current = _Attached(body)
            logger.info("Following %s (pid %s), started from a terminal", body.repository, body.pid)
            return self._current.info()

    def detach(self, pid: int) -> None:
        """Stops following the run of process ``pid``: it has ended."""
        with self._lock:
            if isinstance(self._current, _Attached) and self._current.body.pid == pid:
                self._current.port = None

    def stop(self, *, wait: bool = True) -> RunInfo:
        """Stops the current run: asks it to abort and close, then ends it, and all it started, if it has not within
        ``settings.stop_grace`` seconds. Without ``wait``, returns once it is asked (the rest happens behind)."""
        current = self._current
        if current is None or not current.alive():
            raise HostError("No run is going.", status=409)
        asked = _ask_to_abort(current)
        logger.info("Stopping the run of %s (pid %s)", current.body.repository, current.info(tail=0).pid)
        grace = self.settings.stop_grace if asked else min(self.settings.stop_grace, 2.0)
        if wait:
            _end(current, grace)
        else:
            threading.Thread(target=_end, args=(current, grace), name="clabe-stop-run", daemon=True).start()
        return current.info()

    def terminal_address(self) -> tuple[int, str]:
        """Where a window reaches the current run's terminal UI (port and token)."""
        current = self._current
        if not isinstance(current, _Launched) or current.terminal is None or not current.alive():
            raise HostError("The current run does not show the terminal UI.", status=404)
        address = current.terminal_address()
        if address is None:
            raise HostError("The run's terminal UI is starting.", status=409)
        return address

    def info(self) -> HostInfo:
        """Who the host is and what it runs."""
        import socket

        from .. import __version__

        current = self._current
        run = current.info(tail=0) if current is not None else None
        busy = run is not None and run.status != "exited"
        owner = self._leased_by()
        status = "busy" if busy else "leased" if owner is not None else "idle"
        return HostInfo(
            hostname=socket.gethostname(),
            rig_id=rig_id(),
            ip=ip_address(),
            clabe_version=__version__,
            terminals=_terminal.available(),
            status=status,
            run=run,
            leased_by=owner,
        )

    def busy(self) -> bool:
        """Whether a run launched (or attached) here is going."""
        return self._current is not None and self._current.alive()


def _new_job() -> Job | None:
    """A job for a launched run: it is never ended with the daemon (a restarted daemon finds the run again)."""
    if sys.platform != "win32":
        return None
    try:
        return Job(kill_on_close=False)
    except OSError as exc:
        logger.warning("Stopping a run will not end what it started: %s", exc)
        return None


def _ask_to_abort(run: "_Launched | _Attached") -> bool:
    """Asks ``run`` to abort, through its API; whether it was asked."""
    import httpx

    port = run.poll_port()
    if port is None:
        return False
    headers = {"Authorization": f"Bearer {run.token}"} if run.token else {}
    try:
        answer = httpx.post(
            f"http://127.0.0.1:{port}/api/abort", json={"reason": "Stopped."}, headers=headers, timeout=5.0
        )
    except httpx.HTTPError as exc:
        logger.info("Could not ask the run to abort: %s", exc)
        return False
    return answer.status_code < 400


def _end(run: "_Launched | _Attached", grace: float) -> None:
    """Closes ``run`` once it has aborted (its closing screen), or ends it after ``grace`` seconds."""
    import httpx

    deadline = time.monotonic() + grace
    port = run.poll_port()
    headers = {"Authorization": f"Bearer {run.token}"} if run.token else {}
    while run.alive() and time.monotonic() < deadline:
        if port is not None:
            try:
                httpx.post(
                    f"http://127.0.0.1:{port}/api/close", json={"client": "daemon"}, headers=headers, timeout=2.0
                )
            except httpx.HTTPError:
                pass
        time.sleep(0.5)
    if run.alive():
        logger.warning("The run did not end within %.0f s: ending it and all it started", grace)
        run.kill()
