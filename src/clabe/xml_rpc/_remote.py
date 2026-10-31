import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from ..apps import Command, CommandResult, identity_parser
from ..git_manager import GitRepository, GitRepositoryMetadata
from ._client import XmlRpcClient
from ._executor import XmlRpcExecutor
from ._scheduled_task import DEFAULT_TASK_NAME, start_task
from .models import PROTOCOL_VERSION, ServerInfoResponse

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


class RemoteRepository:
    """A checkout of this repository on another machine, reached through its XML-RPC server.

    The server is expected to run from the remote checkout (see ``clabe xml-rpc-server --register-task``),
    so commands run in the remote repository and in its Python environment by default.

    Example:
        ```python
        remote = RemoteRepository(
            XmlRpcClient(XmlRpcClientSettings(server_url="http://rig-b:8000", token=token)),
            data_roots={r"D:\\data": r"\\\\rig-b\\data"},
        )
        remote.ensure_running()
        remote.verify()
        fip_app = AindBehaviorServicesBonsaiApp("./Aind.Physiology.Fip/src/main.bonsai", rig=fip_rig, session=session)
        await fip_app.run_async(executor=remote)
        source = remote.to_unc(rf"D:\\data\\{session.session_name}")
        ```
    """

    def __init__(
        self,
        client: XmlRpcClient,
        path: str | None = None,
        *,
        local_path: os.PathLike | str = ".",
        data_roots: Mapping[str, str] | None = None,
        task_name: str = DEFAULT_TASK_NAME,
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
            task_name: Name of the scheduled task that starts the remote server.
        """
        self.client = client
        self._path = path
        self.local_path = local_path
        self.data_roots = {PureWindowsPath(local): PureWindowsPath(unc) for local, unc in (data_roots or {}).items()}
        self.task_name = task_name
        self._info: ServerInfoResponse | None = None

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
            TimeoutError: If the server does not respond within ``timeout``.
        """
        if self.client.ping():
            return
        logger.info("Server on %s is not responding; starting scheduled task '%s'", self.host, self.task_name)
        start_task(self.task_name, host=self.host)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            if self.client.ping():
                logger.info("Server on %s is running", self.host)
                return
        raise TimeoutError(f"Server on {self.host} did not respond within {timeout} seconds of starting it")

    def verify(self, allow_dirty: bool = False) -> None:
        """Checks that the remote repository runs the same code as the local one.

        Compares the RPC protocol and clabe versions (which catches an environment that was not
        synced), the server's working directory, and the repository URL and commit of the repository
        and each submodule. Because the lockfile is tracked, a matching clean commit also implies the
        same locked dependencies.

        Args:
            allow_dirty: Whether to accept uncommitted changes in the remote repository.

        Raises:
            RepositoryMismatchError: Listing every difference found.
        """
        info = self.info(refresh=True)
        problems: list[str] = []
        if info.protocol_version != PROTOCOL_VERSION:
            problems.append(f"RPC protocol version {info.protocol_version} != local {PROTOCOL_VERSION}")
        if info.clabe_version != __version__:
            problems.append(f"clabe version {info.clabe_version} != local {__version__}")
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
        path = PureWindowsPath(path)
        # Most specific root first, so nested roots map to their own share
        for root in sorted(self.data_roots, key=lambda r: len(r.parts), reverse=True):
            if path.is_relative_to(root):
                return self.data_roots[root] / path.relative_to(root)
        raise ValueError(f"{path} is not under any data root of {self.host}: {[str(r) for r in self.data_roots]}")

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
