import logging
import os
import socket
import time
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any, Self
from urllib.parse import urlparse

from aind_behavior_services import Session

from .. import __version__
from ..apps import Command, CommandResult, identity_parser
from ..git_manager import GitRepository, GitRepositoryMetadata
from ._client import XmlRpcClient, XmlRpcClientSettings
from ._executor import XmlRpcExecutor
from ._scheduled_task import TASK_NAME, start_task
from .models import DEFAULT_PORT, ServerInfoResponse

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


def to_unc(path: os.PathLike | str, data_roots: Mapping[str, str]) -> PureWindowsPath:
    """Translates a path on another machine to the network share path it is reached by.

    Needs no connection to that machine, e.g. to find the data of a session recorded there earlier.

    Args:
        path: A path on the other machine, under one of the ``data_roots``.
        data_roots: Maps data directories on that machine to the network shares exposing them,
            e.g. ``{r"D:\\data": r"\\\\rig-b\\data"}``.

    Returns:
        The same location under its network share.

    Raises:
        ValueError: If the path is not under any of the ``data_roots``.
    """
    path = PureWindowsPath(path)
    roots = {PureWindowsPath(local): PureWindowsPath(unc) for local, unc in data_roots.items()}
    # Most specific root first, so nested roots map to their own share
    for root in sorted(roots, key=lambda r: len(r.parts), reverse=True):
        if path.is_relative_to(root):
            return roots[root] / path.relative_to(root)
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
    """A checkout of this repository on another machine, reached through its XML-RPC server.

    The server is expected to run from the remote checkout (see ``clabe xml-rpc-server register-task``),
    so commands run in the remote repository and in its Python environment by default.

    Example:
        ```python
        remote = RemoteRepository(
            XmlRpcClient(XmlRpcClientSettings(server_url="http://rig-b:8000", token=token)),
            data_roots={r"D:\\data": r"\\\\rig-b\\data"},
        )
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
        client: XmlRpcClient,
        path: str | None = None,
        *,
        local_path: os.PathLike | str = ".",
        data_roots: Mapping[str, str] | None = None,
    ) -> None:
        """
        Initializes the remote repository.

        Args:
            client: Client connected to the remote machine's XML-RPC server.
            path: Path of the repository on the remote machine. Defaults to the server's working directory.
                When given, :meth:`verify` checks that the server runs from it.
            local_path: Path of the local repository the remote one is compared against.
            data_roots: Maps data directories on the remote machine to the network shares exposing them,
                e.g. ``{r"D:\\data": r"\\\\rig-b\\data"}``. Used by :meth:`to_unc`.
        """
        self.client = client
        self._path = path
        self.local_path = local_path
        self.data_roots = {PureWindowsPath(local): PureWindowsPath(unc) for local, unc in (data_roots or {}).items()}
        self._info: ServerInfoResponse | None = None
        self._session: Session | None = None
        self._data_directory: PureWindowsPath | None = None

    @classmethod
    def from_computer_name(
        cls,
        computer_name: str,
        port: int = DEFAULT_PORT,
        path: str | None = None,
        *,
        local_path: os.PathLike | str = ".",
        data_roots: Mapping[str, str] | None = None,
    ) -> Self:
        """Connects to the server on a machine by its computer name, e.g. a rig's ``computer_name``.

        The remaining client settings, such as the token, are read from the ``xml_rpc_client`` section.

        Args:
            computer_name: Network name of the remote machine.
            port: Port the remote server listens on.
            path: Path of the repository on the remote machine. See :meth:`__init__`.
            local_path: Path of the local repository the remote one is compared against.
            data_roots: Maps data directories on the remote machine to the network shares exposing them.
        """
        client = XmlRpcClient(XmlRpcClientSettings(server_url=f"http://{computer_name}:{port}"))
        return cls(client, path, local_path=local_path, data_roots=data_roots)

    @property
    def host(self) -> str:
        """Hostname of the remote machine, as given in the server URL."""
        host = urlparse(str(self.client.settings.server_url)).hostname
        if host is None:
            raise ValueError(f"No hostname in server URL {self.client.settings.server_url}")
        return host

    def info(self, refresh: bool = False) -> ServerInfoResponse:
        """Returns the remote server's information, fetching it on first use or when ``refresh`` is True."""
        if self._info is None or refresh:
            self._info = self.client.server_info()
        return self._info

    @property
    def path(self) -> str:
        """Path of the repository on the remote machine."""
        return self._path or self.info().working_dir

    def ensure_running(self, timeout: float = 60.0, poll_interval: float = 1.0) -> None:
        """Starts the remote server through its scheduled task if it does not respond.

        Args:
            timeout: Seconds to wait for the server to respond after starting the task.
            poll_interval: Seconds between connection attempts.

        Raises:
            ConnectionError: If the remote machine's name cannot be resolved to an address.
            TimeoutError: If the server does not respond within ``timeout``.
        """
        if self.client.ping():
            return
        _check_resolvable(self.host)
        logger.info("Server on %s is not responding; starting scheduled task '%s'", self.host, TASK_NAME)
        start_task(host=self.host)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            if self.client.ping():
                logger.info("Server on %s is running", self.host)
                return
        raise TimeoutError(f"Server on {self.host} did not respond within {timeout} seconds of starting it")

    def verify(self, allow_dirty: bool = False) -> None:
        """Checks that the remote repository runs the same code as the local one.

        Compares the server's working directory, and the repository URL and commit of the repository
        and each submodule. Because the lockfile is tracked, a matching clean commit also implies the
        same locked dependencies. A different clabe version (e.g. an environment that was not synced)
        is only warned about: the remote is then used on a best-effort basis.

        Args:
            allow_dirty: Whether to accept uncommitted changes in the remote repository.

        Raises:
            RepositoryMismatchError: Listing every difference found.
        """
        info = self.info(refresh=True)
        if info.clabe_version != __version__:
            logger.warning(
                "Remote server on %s runs clabe %s, not %s; continuing on a best-effort basis",
                self.host,
                info.clabe_version,
                __version__,
            )
        problems: list[str] = []
        if self._path is not None and PureWindowsPath(self._path) != PureWindowsPath(info.working_dir):
            problems.append(f"server runs from {info.working_dir}, not {self._path}")
        if problems:
            raise RepositoryMismatchError(f"Remote repository on {self.host} does not match: " + "; ".join(problems))

        result = self._executor().run(
            Command(
                cmd=[info.python_executable, "-m", "clabe.cli", "repository-state", "."], output_parser=identity_parser
            )
        )
        remote = _flatten_commits(GitRepositoryMetadata.model_validate_json(result.stdout or ""))
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

    def to_unc(self, path: os.PathLike | str) -> PureWindowsPath:
        """Translates a path on the remote machine to the network share path other machines reach it by.

        Args:
            path: A path on the remote machine, under one of the ``data_roots``.

        Returns:
            The same location under its network share.

        Raises:
            ValueError: If the path is not under any of the ``data_roots``.
        """
        try:
            return to_unc(path, {str(local): str(unc) for local, unc in self.data_roots.items()})
        except ValueError as e:
            raise ValueError(f"{e} (on {self.host})") from e

    def register_session(self, session: Session, data_directory: os.PathLike | str) -> Self:
        """Registers the session this machine records data for, and creates its session directory there.

        The remote counterpart of :meth:`~clabe.launcher.Launcher.register_session`. Optional: only
        :attr:`data_directory`, :attr:`session_directory` and :attr:`session_directory_unc` need it.

        Args:
            session: The session, shared with the launcher.
            data_directory: Root data directory on the remote machine, e.g. the remote rig's ``data_directory``.

        Returns:
            Self: The updated instance.

        Raises:
            ValueError: If a session is already registered, or the session has no name.
        """
        if self._session is not None:
            raise ValueError("Session already registered.")
        if session.session_name is None:
            raise ValueError("session.session_name is not set.")
        self._session = session
        self._data_directory = PureWindowsPath(data_directory)
        # Created by a command on the remote machine, since its local paths are not reachable from here
        mkdir = "import pathlib, sys; pathlib.Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)"
        self.run(
            Command(
                cmd=[self.info().python_executable, "-c", mkdir, str(self.session_directory)],
                output_parser=identity_parser,
            )
        )
        logger.debug("Created session directory on %s at %s", self.host, self.session_directory)
        return self

    @property
    def data_directory(self) -> PureWindowsPath:
        """Root data directory on the remote machine, set by :meth:`register_session`."""
        if self._data_directory is None:
            raise ValueError("Data directory is not set. Call register_session first.")
        return self._data_directory

    @property
    def session_directory(self) -> PureWindowsPath:
        """Session directory as the remote machine sees it, e.g. to configure where an app saves its data."""
        if self._session is None or self._session.session_name is None:
            raise ValueError("Session is not registered. Call register_session first.")
        return self.data_directory / self._session.session_name

    @property
    def session_directory_unc(self) -> PureWindowsPath:
        """Session directory as other machines reach it through its network share, e.g. for watchdog or mappers.

        Raises:
            ValueError: If no session is registered, or the data directory is not under any of the ``data_roots``.
        """
        return self.to_unc(self.session_directory)

    def _executor(self) -> XmlRpcExecutor:
        """Returns an executor that runs commands in the remote repository, mapping local repository paths to it."""
        settings = self.client.settings
        return XmlRpcExecutor(
            self.client,
            timeout=settings.timeout,
            poll_interval=settings.poll_interval,
            monitor=settings.monitor,
            cwd=self.path,
            path_mapping={str(Path(self.local_path).resolve()): self.path},
        )

    def run(self, command: Command[Any]) -> CommandResult:
        """Runs a command in the remote repository and waits for it.

        Makes the remote repository an :class:`~clabe.apps.Executor`, so any app runs on it unchanged,
        e.g. ``app.run(executor=remote)``. Paths under the local repository are rewritten to the remote
        one, and the command's ``inputs`` are copied to the remote machine.

        Args:
            command: The command to run.

        Returns:
            The command's result.

        Raises:
            CommandError: If the command exits with a non-zero code.
        """
        return self._executor().run(command)

    async def run_async(self, command: Command[Any]) -> CommandResult:
        """Asynchronous version of :meth:`run`, e.g. ``await app.run_async(executor=remote)``."""
        return await self._executor().run_async(command)
