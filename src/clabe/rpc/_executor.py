import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

from ..apps._base import Command, CommandResult
from .models import JobStatus

if TYPE_CHECKING:
    from ._client import RpcClient

logger = logging.getLogger(__name__)


class RpcExecutor:
    """
    Executor that runs commands remotely via RPC client.

    This executor implements both synchronous and asynchronous execution interfaces
    by wrapping an RPC client. Commands are submitted to the remote RPC server for
    execution and results are retrieved when complete.

    Attributes:
        client: The RPC client instance for communication with the remote server

    Example:
        ```python
        from clabe.rpc._client import RpcClient, RpcClientSettings

        # Create RPC client
        settings = RpcClientSettings(
            server_url="http://localhost:8000",
            token="your-auth-token"
        )
        client = RpcClient(settings)

        # Create executor
        executor = RpcExecutor(client)

        # Use as synchronous executor
        cmd = Command(cmd="echo hello", output_parser=identity_parser)
        result = executor.run(cmd)

        # Use as asynchronous executor
        result = await executor.run_async(cmd)
        ```
    """

    def __init__(
        self, client: "RpcClient", timeout: Optional[float] = None, poll_interval: Optional[float] = None
    ) -> None:
        """
        Initialize the RPC executor.

        Args:
            client: RPC client instance for communication with the remote server
            timeout: Maximum time to wait for command completion (uses client default if None)
            poll_interval: Polling interval for job status checks (uses client default if None)

        Example:
            ```python
            from clabe.rpc._client import RpcClient, RpcClientSettings

            settings = RpcClientSettings(
                server_url="http://localhost:8000",
                token="auth-token"
            )
            client = RpcClient(settings)
            executor = RpcExecutor(client, timeout=60.0, poll_interval=1.0)
            ```
        """
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval

        logger.info(f"RPC executor initialized for server: {client.settings.server_url}")

    def run(self, command: Command[Any]) -> CommandResult:
        """
        Execute the command synchronously via RPC and return the result.

        Args:
            command: The command to execute remotely

        Returns:
            CommandResult with execution output and exit code

        Raises:
            Exception: If command submission or execution fails
            TimeoutError: If command doesn't complete within timeout

        Example:
            ```python
            executor = RpcExecutor(settings)
            cmd = Command(cmd="python --version", output_parser=identity_parser)
            result = executor.run(cmd)
            print(f"Output: {result.stdout}")
            ```
        """
        logger.info(f"Executing command via RPC: {command.cmd}")

        job_result = self.client.run_command(command.cmd, timeout=self.timeout)

        result = CommandResult(stdout=job_result.stdout, stderr=job_result.stderr, exit_code=job_result.returncode or 0)
        result.check_returncode()
        logger.info(f"RPC command completed with exit code: {result.exit_code}")
        return result

    async def run_async(self, command: Command[Any]) -> CommandResult:
        """
        Execute the command asynchronously via RPC and return the result.

        Args:
            command: The command to execute remotely

        Returns:
            CommandResult with execution output and exit code

        Raises:
            Exception: If command submission or execution fails
            TimeoutError: If command doesn't complete within timeout

        Example:
            ```python
            executor = RpcExecutor(settings)
            cmd = Command(cmd="sleep 5 && echo done", output_parser=identity_parser)
            result = await executor.run_async(cmd)
            print(f"Output: {result.stdout}")
            ```
        """
        logger.info(f"Executing command asynchronously via RPC: {command.cmd}")
        submission = self.client.submit_command(command.cmd)
        if submission.job_id is None:
            raise RuntimeError("Job submission failed: no job ID returned")

        job_result = await self._wait_for_result_async(submission.job_id)

        result = CommandResult(stdout=job_result.stdout, stderr=job_result.stderr, exit_code=job_result.returncode or 0)
        result.check_returncode()

        logger.info(f"RPC async command completed with exit code: {result.exit_code}")
        return result

    async def _wait_for_result_async(self, job_id: str):
        """
        Asynchronously wait for a job to complete and return the result.

        Args:
            job_id: Job ID to wait for

        Returns:
            JobResult with execution details

        Raises:
            TimeoutError: If job doesn't complete within timeout
        """

        timeout = self.timeout or self.client.settings.timeout
        poll_interval = self.poll_interval or self.client.settings.poll_interval
        start_time = time.time()

        while time.time() - start_time < timeout:
            await asyncio.sleep(poll_interval)
            # this is synchronous but should be fast
            result = self.client.get_result(job_id)
            if result.status == JobStatus.DONE:
                return result

        raise TimeoutError(f"Job {job_id} did not complete within {timeout} seconds")
