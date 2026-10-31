import asyncio
import logging
import os
import re
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..apps._base import Command, CommandResult
from ._staged_command import StagedCommand, StagedFile
from .models import JobStatus

if TYPE_CHECKING:
    from ._client import XmlRpcClient

logger = logging.getLogger(__name__)


class XmlRpcExecutor:
    """
    Executor that runs commands remotely via an XML-RPC client.

    This executor implements both synchronous and asynchronous execution interfaces
    by wrapping an XML-RPC client. Commands are submitted to the remote server for
    execution and results are retrieved when complete. Inputs a command declares
    (``Command.inputs``) are copied to the server first, and ``path_mapping`` rewrites
    local path prefixes to their location on the server. To run apps in a checkout of
    this repository on another machine, use :class:`~clabe.xml_rpc.RemoteRepository`, which
    builds on this executor.

    Attributes:
        client: The XML-RPC client instance for communication with the remote server

    Example:
        ```python
        from clabe.apps import Command, identity_parser
        from clabe.xml_rpc import XmlRpcClient, XmlRpcClientSettings, XmlRpcExecutor

        # Create the client
        settings = XmlRpcClientSettings(
            server_url="http://localhost:8000",
            token="your-auth-token"
        )
        client = XmlRpcClient(settings)

        # Create executor
        executor = XmlRpcExecutor(client)

        # Use as synchronous executor
        cmd = Command(cmd=["echo", "hello"], output_parser=identity_parser)
        result = executor.run(cmd)

        # Use as asynchronous executor
        result = await executor.run_async(cmd)
        ```
    """

    def __init__(
        self,
        client: "XmlRpcClient",
        timeout: float | None = None,
        poll_interval: float | None = None,
        monitor: bool = True,
        cwd: str | None = None,
        path_mapping: Mapping[str, str] | None = None,
    ) -> None:
        """
        Initialize the RPC executor.

        Args:
            client: RPC client instance for communication with the remote server
            timeout: Maximum time to wait for command completion (uses client default if None)
            poll_interval: Polling interval for job status checks (uses client default if None)
            monitor: If True, timeout becomes a liveness check window. As long as the job
                is confirmed running within the timeout period, waiting continues indefinitely.
                If False, timeout is the total execution time limit.
            cwd: Working directory on the server. Defaults to the server's working directory.
            path_mapping: Local path prefixes to rewrite in commands, mapped to their location on the
                server, e.g. the local repository to the remote one.

        Example:
            ```python
            from clabe.xml_rpc import XmlRpcClient, XmlRpcClientSettings, XmlRpcExecutor

            settings = XmlRpcClientSettings(
                server_url="http://localhost:8000",
                token="auth-token"
            )
            client = XmlRpcClient(settings)
            executor = XmlRpcExecutor(client, timeout=60.0, poll_interval=1.0)
            ```
        """
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.monitor = monitor
        self.cwd = cwd
        self.path_mapping = dict(path_mapping or {})
        self._python_executable: str | None = None

        logger.info("RPC executor initialized for server: %s", client.settings.server_url)

    def _map_paths(self, arg: str) -> str:
        """Rewrites local path prefixes in a command argument to their location on the server."""
        flags = re.IGNORECASE if os.name == "nt" else 0
        for local, remote in self.path_mapping.items():
            pattern = re.escape(local.rstrip("\\/")) + r"(?=[\\/]|$)"  # whole components: C:\repo is not C:\repo2
            replacement = remote.rstrip("\\/")
            # A function replacement keeps the backslashes of Windows paths literal
            arg = re.sub(pattern, lambda _, r=replacement: r, arg, flags=flags)
        return arg

    def _prepare(self, command: Command[Any]) -> list[str]:
        """Builds the command line to submit to the server.

        Paths in ``path_mapping`` are rewritten. When the command has ``inputs``, their contents are
        uploaded together with the command, which then runs through the staged command runner so the
        inputs are written on the server and the command points at them.
        """
        if not command.inputs:
            return [self._map_paths(arg) for arg in command.cmd] if self.path_mapping else command.cmd

        placeholders = {str(path): name for name, path in command.inputs.items()}

        def substitute(arg: str) -> str:
            # Inputs first: they may live under a mapped prefix, e.g. a temporary directory in the repository
            for local, name in placeholders.items():
                arg = arg.replace(local, f"{{{name}}}")
            return self._map_paths(arg)

        spec = StagedCommand(
            cmd=[substitute(arg) for arg in command.cmd],
            files={
                name: StagedFile(filename=Path(path).name, content=Path(path).read_text(encoding="utf-8"))
                for name, path in command.inputs.items()
            },
        )
        upload = self.client.upload_model(spec, f"clabe_staged_{uuid.uuid4().hex}.json")
        if upload.path is None:
            raise RuntimeError("Uploading the staged command returned no path")
        if self._python_executable is None:
            self._python_executable = self.client.server_info().python_executable
        return [self._python_executable, "-m", "clabe.xml_rpc._staged_command", upload.path]

    def run(self, command: Command[Any]) -> CommandResult:
        """
        Execute the command synchronously via RPC and return the result.

        Args:
            command: The command to execute remotely (as a list of strings)

        Returns:
            CommandResult with execution output and exit code

        Raises:
            Exception: If command submission or execution fails
            TimeoutError: If command doesn't complete within timeout

        Example:
            ```python
            executor = XmlRpcExecutor(client)
            cmd = Command(cmd=["python", "--version"], output_parser=identity_parser)
            result = executor.run(cmd)
            print(f"Output: {result.stdout}")
            ```
        """
        logger.info("Executing command via RPC: %s", command.cmd)

        job_result = self.client.run_command(self._prepare(command), timeout=self.timeout, cwd=self.cwd)

        result = CommandResult(stdout=job_result.stdout, stderr=job_result.stderr, exit_code=job_result.returncode or 0)
        result.check_returncode()
        logger.info("RPC command completed with exit code: %s", result.exit_code)
        return result

    async def run_async(self, command: Command[Any]) -> CommandResult:
        """
        Execute the command asynchronously via RPC and return the result.

        Args:
            command: The command to execute remotely (as a list of strings)

        Returns:
            CommandResult with execution output and exit code

        Raises:
            Exception: If command submission or execution fails
            TimeoutError: If command doesn't complete within timeout

        Example:
            ```python
            executor = XmlRpcExecutor(client)
            cmd = Command(cmd=["python", "-c", "print('done')"], output_parser=identity_parser)
            result = await executor.run_async(cmd)
            print(f"Output: {result.stdout}")
            ```
        """
        logger.info("Executing command asynchronously via RPC: %s", command.cmd)
        cmd = await asyncio.to_thread(self._prepare, command)
        submission = self.client.submit_command(cmd, cwd=self.cwd)
        if submission.job_id is None:
            raise RuntimeError("Job submission failed: no job ID returned")

        job_result = await self._wait_for_result_async(submission.job_id)

        result = CommandResult(stdout=job_result.stdout, stderr=job_result.stderr, exit_code=job_result.returncode or 0)
        result.check_returncode()

        logger.info("RPC async command completed with exit code: %s", result.exit_code)
        return result

    async def _wait_for_result_async(self, job_id: str):
        """
        Asynchronously wait for a job to complete and return the result.

        Args:
            job_id: Job ID to wait for

        Returns:
            JobResult with execution details

        Raises:
            TimeoutError: If job doesn't complete within timeout (or if monitor mode
                is enabled and the job stops responding within the timeout window)
        """

        timeout = self.timeout or self.client.settings.timeout
        poll_interval = self.poll_interval or self.client.settings.poll_interval
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")

            await asyncio.sleep(poll_interval)
            # this is synchronous but should be fast
            result = self.client.get_result(job_id)
            if result.status == JobStatus.DONE:
                return result

            # In monitor mode, reset timer if job is still running
            if self.monitor and self.client.is_running(job_id):
                logger.debug("Job %s is still running; resetting timeout timer", job_id)
                start_time = time.time()
