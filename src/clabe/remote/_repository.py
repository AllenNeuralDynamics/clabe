"""A checkout of this repository on another machine, reached through its clabe daemon."""

import atexit
import logging
import os
import socket
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlparse

from aind_behavior_services import Session

from .. import __version__
from ..apps import Command, CommandResult, identity_parser
from ..daemon._install import TASK_NAME
from ..daemon._install import start as start_task
from ..repository import GitRepository, GitRepositoryMetadata
from ._client import WorkerClient, WorkerClientSettings
from ._executor import RemoteExecutor
from ._models import DEFAULT_PORT, JobSummary, WorkerInfo
from ._provenance import record_worker

if TYPE_CHECKING:
    from ..workflow import RunStatus

logger = logging.getLogger(__name__)


class RepositoryMismatchError(RuntimeError):
    """Raised when a remote repository does not match the local one."""


def _flatten_commits(metadata: GitRepositoryMetadata, key: str = ".") -> dict[str, GitRepositoryMetadata]:
    """Maps the repository ('.') and each submodule, keyed by its position in the submodule tree, to its metadata."""
    flat = {key: metadata}
    for submodule in metadata.submodules:
        flat.update(_flatten_commits(submodule, submodule.name if key == "." else f"{key}/{submodule.name}"))
    return flat


def _normalize_url(url: str | None) -> str | None:
    """Normalizes a remote URL so the same repository compares equal however it was cloned."""
    if url is None:
        return None
    return url.rstrip("/").removesuffix(".git").lower()


def to_unc(
    path: os.PathLike | str, data_roots: Mapping[str, str] | None = None, host: str | None = None
) -> PureWindowsPath:
    """Translates a path on another machine to the network share path it is reached by.

    Needs no connection to that machine, e.g. to find the data of a session recorded there earlier. A path under
    none of the ``data_roots`` is reached through its drive's administrative share on ``host``, e.g. ``D:\\data``
    on ``rig-b`` as ``\\\\rig-b\\D$\\data``, which needs administrator rights on that machine.

    Args:
        path: An absolute path on the other machine.
        data_roots: Maps data directories on that machine to the network shares exposing them,
            e.g. ``{r"D:\\data": r"\\\\rig-b\\data"}``. Takes precedence over the administrative share.
        host: Name of the other machine, for its administrative shares.

    Returns:
        The same location under its network share.

    Raises:
        ValueError: If the path is under none of the ``data_roots`` and there is no ``host`` or drive to fall back on.
    """
    path = PureWindowsPath(path)
    roots = {PureWindowsPath(local): PureWindowsPath(unc) for local, unc in (data_roots or {}).items()}
    # Most specific root first, so nested roots map to their own share
    for root in sorted(roots, key=lambda r: len(r.parts), reverse=True):
        if path.is_relative_to(root):
            return roots[root] / path.relative_to(root)
    # A drive letter path, e.g. D:\data, but not a path that already is a share
    if host is not None and len(path.drive) == 2 and path.drive.endswith(":"):
        return PureWindowsPath(f"//{host}/{path.drive[0]}$/") / path.relative_to(path.anchor)
    raise ValueError(f"{path} is not under any data root: {[str(r) for r in roots]}")


def _check_resolvable(host: str) -> None:
    """Raises a clear error when ``host`` cannot be resolved to an address, e.g. a misspelled computer name."""
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ConnectionError(
            f"Cannot resolve '{host}' to an address. Check the computer name, and that it is on the same network."
        ) from e


class RemoteRepository:
    """A checkout of this repository on another machine, reached through that machine's clabe daemon (its worker).

    The repository must be one the remote daemon offers (found under its roots, or in
    ``DAEMON_CLABE_REPOSITORIES``); commands run in it and in its environment by default. The first command (or `lease`) takes the worker's lease, so no other
    run uses the machine meanwhile; it is renewed in the background and given back by `release` (or at exit).

    Example:
        ```python
        remote = RemoteRepository.from_computer_name("rig-b", data_roots={r"D:\\data": r"\\\\rig-b\\data"})
        remote.ensure_running()
        remote.verify()
        remote.register_session(session, fip_rig.data_directory)
        fip_app = AindBehaviorServicesBonsaiApp("./Aind.Physiology.Fip/src/main.bonsai", rig=fip_rig, session=session)
        await fip_app.run_async(executor=remote)
        source = remote.session_directory_unc  # e.g. \\\\rig-b\\data\\<session name>
        ```
    """

    def __init__(
        self,
        client: WorkerClient,
        path: str | None = None,
        *,
        local_path: os.PathLike | str = ".",
        data_roots: Mapping[str, str] | None = None,
        detach_on_abort: bool = False,
    ) -> None:
        """
        Initializes the remote repository.

        Args:
            client: Client of the remote machine's worker (its ``repository`` setting names the checkout).
            path: Path of the repository on the remote machine. Defaults to the worker's repository.
                When given, `verify` checks that it is the worker's repository.
            local_path: Path of the local repository the remote one is compared against.
            data_roots: Maps data directories on the remote machine to the network shares exposing them,
                e.g. ``{r"D:\\data": r"\\\\rig-b\\data"}``. Optional: by default `to_unc` uses the folders the
                machine shares (its daemon lists them), then the drive's administrative share. Entries here take
                precedence, e.g. for a share reached through another server name.
            detach_on_abort: Keep the commands running on the remote machine when the run does not complete (e.g.
                a recording that must outlive the launcher). By default they are stopped with the run.
        """
        self.client = client
        self.detach_on_abort = detach_on_abort
        self._path = path
        self.local_path = local_path
        self.data_roots = {PureWindowsPath(local): PureWindowsPath(unc) for local, unc in (data_roots or {}).items()}
        self._info: WorkerInfo | None = None
        self._shares: dict[PureWindowsPath, PureWindowsPath] | None = None
        self._session: Session | None = None
        self._data_directory: PurePath | None = None

    @classmethod
    def from_computer_name(
        cls,
        computer_name: str,
        port: int = DEFAULT_PORT,
        path: str | None = None,
        *,
        repository: str | None = None,
        local_path: os.PathLike | str = ".",
        data_roots: Mapping[str, str] | None = None,
    ) -> Self:
        """Connects to the daemon of a machine by its computer name, e.g. a rig's ``computer_name``: nothing to
        configure. The other client settings (a token, timeouts), when needed, come from the experiment's
        ``worker_client`` section.

        Args:
            computer_name: Network name of the remote machine.
            port: Port of its daemon.
            path: Path of the repository on the remote machine. See `__init__`.
            repository: The remote daemon's name for the repository; by default this repository's folder name.
            local_path: Path of the local repository the remote one is compared against.
            data_roots: Maps data directories on the remote machine to the network shares exposing them.
                Defaults to the drives' administrative shares.
        """
        settings = WorkerClientSettings(
            url=f"http://{computer_name}:{port}", repository=repository or Path(local_path).resolve().name
        )
        return cls(WorkerClient(settings), path, local_path=local_path, data_roots=data_roots)

    @property
    def host(self) -> str:
        """Hostname of the remote machine, as given in the worker's URL."""
        host = urlparse(self.client.base_url).hostname
        if host is None:
            raise ValueError(f"No hostname in worker URL {self.client.base_url}")
        return host

    def info(self, refresh: bool = False) -> WorkerInfo:
        """Returns the remote worker's information, fetching it on first use or when ``refresh`` is True."""
        if self._info is None or refresh:
            self._info = self.client.info()
        return self._info

    @property
    def path(self) -> str:
        """Path of the repository on the remote machine."""
        return self._path or self.info().working_dir

    def lease(self, run_id: str | None = None) -> None:
        """Takes the remote machine for this run (the first command does it anyway); renewed until released.

        Args:
            run_id: The run to take it for; by default the running one. A restarted launcher passes the run id of
                the session it recovers (see `reattach`).

        Raises:
            WorkerError: 423 if another run holds the machine, 409 if it runs an experiment of its own.
        """
        if self.client.leased:
            return
        from ..workflow._records import ACTIVE_STEP

        context = ACTIVE_STEP.get()
        run_id = run_id or (context.run.id if context is not None else None)
        self.client.lease(owner=f"{socket.gethostname()} ({Path(self.local_path).resolve().name})", run_id=run_id)
        atexit.register(self.client.release)
        if context is not None:
            context.run.add_finalizer(self._run_ended)
            record_worker(self.client)
        logger.info("Took %s for this run", self.host)

    def reattach(self, run_id: str) -> list[JobSummary]:
        """Takes the remote machine back for run ``run_id`` and returns that run's jobs still there.

        For recovering a session after the launcher crashed: its jobs kept running on the remote machine, and for a
        grace period after its lease expired only the same run may take the machine back. The run id and the
        machine are in the crashed session's ``run.json`` (``run_id``, ``workers``). Collect each job's result with
        `wait_for`.
        """
        self.lease(run_id)
        jobs = self.client.jobs_of(run_id)
        logger.info("Reattached to %s: %d job(s) of run %s", self.host, len(jobs), run_id)
        return jobs

    def wait_for(self, job_id: str) -> CommandResult:
        """Waits for a job already on the remote machine (see `reattach`) and returns its result."""
        return self._executor().wait(job_id)

    async def wait_for_async(self, job_id: str) -> CommandResult:
        """Asynchronous `wait_for`; cancelling it stops the job."""
        return await self._executor().wait_async(job_id)

    def _run_ended(self, status: "RunStatus") -> None:
        """Gives the machine back when the run ends; stops what still runs there unless the run completed (or
        ``detach_on_abort``)."""
        from ..workflow import RunStatus

        stop = status is not RunStatus.COMPLETED and not self.detach_on_abort
        logger.info("Run ended (%s): releasing %s%s", status.value, self.host, " and stopping its jobs" if stop else "")
        self.release(stop_jobs=stop)

    def release(self, *, stop_jobs: bool = False) -> None:
        """Gives the remote machine back; ``stop_jobs`` also stops what still runs there."""
        self.client.release(stop_jobs=stop_jobs)

    def ensure_running(self, timeout: float = 60.0, poll_interval: float = 1.0) -> None:
        """Starts the remote daemon through its scheduled task (``clabe daemon install``) if it does not respond.

        Args:
            timeout: Seconds to wait for the daemon to respond after starting the task.
            poll_interval: Seconds between connection attempts.

        Raises:
            ConnectionError: If the remote machine's name cannot be resolved to an address.
            TimeoutError: If the daemon does not respond within ``timeout``.
        """
        if self.client.ping():
            return
        _check_resolvable(self.host)
        logger.info("The daemon on %s is not responding; starting its scheduled task '%s'", self.host, TASK_NAME)
        start_task(host=self.host)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            if self.client.ping():
                logger.info("The daemon on %s is running", self.host)
                return
        raise TimeoutError(f"The daemon on {self.host} did not respond within {timeout} seconds of starting it")

    def verify(self, allow_dirty: bool = False) -> None:
        """Checks that the remote repository runs the same code as the local one.

        Compares the worker's repository path, and the repository URL and commit of the repository and each
        submodule. Because the lockfile is tracked, a matching clean commit also implies the same locked
        dependencies. A different clabe version of the daemon is only warned about.

        Args:
            allow_dirty: Whether to accept uncommitted changes in the remote repository.

        Raises:
            RepositoryMismatchError: Listing every difference found.
        """
        info = self.info(refresh=True)
        if info.clabe_version != __version__:
            logger.warning(
                "The daemon on %s runs clabe %s, not %s; continuing on a best-effort basis",
                self.host,
                info.clabe_version,
                __version__,
            )
        problems: list[str] = []
        if self._path is not None and self._remote_path(self._path) != self._remote_path(info.working_dir):
            problems.append(f"the worker's repository is {info.working_dir}, not {self._path}")
        if problems:
            raise RepositoryMismatchError(f"Remote repository on {self.host} does not match: " + "; ".join(problems))

        result = self._executor().run(
            Command(
                cmd=[info.python_executable, "-m", "clabe.cli", "repository-state", "."], output_parser=identity_parser
            )
        )
        remote_state = GitRepositoryMetadata.model_validate_json(result.stdout or "")
        record_worker(self.client, lambda record: setattr(record, "repository_state", remote_state))
        remote = _flatten_commits(remote_state)
        local = _flatten_commits(GitRepository(self.local_path).get_metadata())

        if _normalize_url(remote["."].url) != _normalize_url(local["."].url):
            problems.append(f"remote URL {remote['.'].url} != local {local['.'].url}")
        for key in sorted(local.keys() | remote.keys()):
            if key not in remote or key not in local:
                problems.append(f"{key}: only present {'locally' if key in local else 'remotely'}")
                continue
            if remote[key].commit_sha != local[key].commit_sha:
                problems.append(f"{key}: commit {remote[key].commit_sha} != local {local[key].commit_sha}")
            if remote[key].is_dirty and not allow_dirty:
                problems.append(f"{key}: has uncommitted changes")
        if problems:
            raise RepositoryMismatchError(f"Remote repository on {self.host} does not match: " + "; ".join(problems))
        logger.info("Remote repository on %s matches the local one (%s)", self.host, local["."].commit_sha)

    def _remote_path(self, path: os.PathLike | str) -> PurePath:
        """``path`` as the remote machine spells paths (Windows or POSIX, as its worker reports)."""
        return PurePosixPath(path) if self.info().os_name == "posix" else PureWindowsPath(path)

    def to_unc(self, path: os.PathLike | str) -> PureWindowsPath:
        """Translates a path on the remote machine to the network share path other machines reach it by.

        Paths under none of the ``data_roots`` are reached through the drive's administrative share; see
        `to_unc`.

        Raises:
            ValueError: If the path is under none of the ``data_roots`` and has no drive letter.
        """
        try:
            return to_unc(path, {str(local): str(unc) for local, unc in self._roots().items()}, host=self.host)
        except ValueError as e:
            raise ValueError(f"{e} (on {self.host})") from e

    def _roots(self) -> dict[PureWindowsPath, PureWindowsPath]:
        """The data roots `to_unc` uses: the ``data_roots`` given, then the folders the machine shares (asked of its
        daemon once; none when it cannot tell)."""
        if self._shares is None:
            try:
                listed = self.client.shares()
            except Exception:
                logger.debug("Could not list the shares of %s", self.host, exc_info=True)
                listed = []
            self._shares = {PureWindowsPath(s.path): PureWindowsPath(f"//{self.host}/{s.name}/") for s in listed}
        return {**self._shares, **self.data_roots}

    def register_session(self, session: Session, data_directory: os.PathLike | str) -> Self:
        """Registers the session this machine records data for, and creates its session directory there.

        The remote counterpart of [`register_session`][clabe.launcher.Launcher.register_session]. Optional: only
        `data_directory`, `session_directory` and `session_directory_unc` need it.

        Raises:
            ValueError: If a session is already registered, or the session has no name.
        """
        if self._session is not None:
            raise ValueError("Session already registered.")
        if session.session_name is None:
            raise ValueError("session.session_name is not set.")
        self._session = session
        self._data_directory = self._remote_path(data_directory)
        self.mkdir(self.session_directory)
        logger.debug("Created session directory on %s at %s", self.host, self.session_directory)
        try:
            unc: str | None = str(self.session_directory_unc)
        except ValueError:
            unc = None
        local = str(self.session_directory)

        def note(record) -> None:
            """Where this machine keeps the session's data, in the run's record of it (``run.json``)."""
            record.session_directory, record.session_directory_unc = local, unc

        record_worker(self.client, note)
        return self

    def mkdir(self, path: os.PathLike | str) -> None:
        """Creates a directory, and its missing parents, on the remote machine."""
        self._run_python("import pathlib, sys; pathlib.Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)", path)

    def write_file(self, path: os.PathLike | str, content: str | bytes) -> None:
        """Writes a file on the remote machine, creating its directory if needed.

        The content is uploaded to the worker's transfer directory and then moved into place. It is limited to the
        client's ``max_file_size``.

        Args:
            path: The file, as the remote machine sees it. Replaced if it exists.
            content: The content. Text is written as UTF-8.
        """
        name = self._remote_path(path).name
        self.lease()
        with tempfile.TemporaryDirectory() as staging:
            upload_path = Path(staging) / f"clabe_{uuid.uuid4().hex}_{name}"
            upload_path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
            upload = self.client.upload_file(upload_path)
        move = (
            "import pathlib, shutil, sys; target = pathlib.Path(sys.argv[2]); "
            "target.parent.mkdir(parents=True, exist_ok=True); shutil.move(sys.argv[1], target)"
        )
        self._run_python(move, upload.path, path)
        logger.debug("Wrote %s on %s", path, self.host)

    def _run_python(self, code: str, *args: os.PathLike | str) -> None:
        """Runs Python ``code`` with ``args`` on the remote machine, in the repository's environment."""
        self.run(
            Command(
                cmd=[self.info().python_executable, "-c", code, *(str(arg) for arg in args)],
                output_parser=identity_parser,
            )
        )

    @property
    def data_directory(self) -> PurePath:
        """Root data directory on the remote machine, set by `register_session`."""
        if self._data_directory is None:
            raise ValueError("Data directory is not set. Call register_session first.")
        return self._data_directory

    @property
    def session_directory(self) -> PurePath:
        """Session directory as the remote machine sees it, e.g. to configure where an app saves its data."""
        if self._session is None or self._session.session_name is None:
            raise ValueError("Session is not registered. Call register_session first.")
        return self.data_directory / self._session.session_name

    @property
    def session_directory_unc(self) -> PureWindowsPath:
        """Session directory as other machines reach it through its network share, e.g. for watchdog or mappers."""
        return self.to_unc(self.session_directory)

    def _executor(self) -> RemoteExecutor:
        """Returns an executor that runs commands in the remote repository, mapping local repository paths to it."""
        self.lease()
        return RemoteExecutor(
            self.client, cwd=self.path, path_mapping={str(Path(self.local_path).resolve()): self.path}
        )

    def run(self, command: Command[Any]) -> CommandResult:
        """Runs a command in the remote repository and waits for it (an [`Executor`][clabe.apps.Executor]).

        Paths under the local repository are rewritten to the remote one, and the command's ``inputs`` are copied
        to the remote machine.

        Raises:
            CommandError: If the command exits with a non-zero code.
        """
        return self._executor().run(command)

    async def run_async(self, command: Command[Any]) -> CommandResult:
        """Asynchronous version of `run`, e.g. ``await app.run_async(executor=remote)``; cancelling it stops
        the command on the remote machine."""
        return await self._executor().run_async(command)
