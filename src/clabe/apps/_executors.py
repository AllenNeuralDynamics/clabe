import asyncio
import os
import subprocess
from typing import Any, Optional

from ._base import AsyncExecutor, Command, CommandResult, ExecutableApp, Executor


class LocalExecutor(Executor):
    """
    Synchronous executor for running commands on the local system.

    Executes commands using subprocess.run with configurable working directory
    and environment variables. Captures both stdout and stderr, and enforces
    return code checking.

    Attributes:
        cwd: Working directory for command execution
        env: Environment variables for the subprocess

    Example:
        ```python
        # Create executor with default settings
        executor = LocalExecutor()

        # Create executor with custom working directory
        executor = LocalExecutor(cwd="/path/to/workdir")

        # Create executor with custom environment
        executor = LocalExecutor(env={"KEY": "value"})

        # Execute a command
        cmd = Command(cmd="echo hello", output_parser=identity_parser)
        result = executor.run(cmd)
        ```
    """

    def __init__(
        self, cwd: os.PathLike | None = None, env: dict[str, str] | None = None, timeout: float | None = None
    ) -> None:
        """Initialize the local executor.

        Args:
            cwd: Working directory for command execution
            env: Environment variables for the subprocess

        """
        self.cwd = cwd or os.getcwd()
        self.env = env
        self.timeout = timeout

    def run(self, command: Command[Any]) -> CommandResult:
        """Execute the command and return the result.
        Args:
            command: The command to execute
        Example:
            ```python
            executor = LocalExecutor()
            cmd = Command(cmd="echo hello", output_parser=identity_parser)
            result = executor.run(cmd)
            ```
        """
        proc = subprocess.run(
            command.cmd, cwd=self.cwd, env=self.env, text=True, capture_output=True, check=False, timeout=self.timeout
        )
        proc.check_returncode()
        return CommandResult(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode)


class AsyncLocalExecutor(AsyncExecutor):
    """
    Asynchronous executor for running commands on the local system.

    Executes commands asynchronously using asyncio.create_subprocess_shell with
    configurable working directory and environment variables. Ideal for long-running
    processes or when multiple commands need to run concurrently.

    Attributes:
        cwd: Working directory for command execution
        env: Environment variables for the subprocess

    Example:
        ```python
        # Create async executor
        executor = AsyncLocalExecutor()

        # Execute a command asynchronously
        cmd = Command(cmd="echo hello", output_parser=identity_parser)
        result = await executor.run_async(cmd)

        # Run multiple commands concurrently
        executor = AsyncLocalExecutor(cwd="/workdir")
        cmd1 = Command(cmd="task1", output_parser=identity_parser)
        cmd2 = Command(cmd="task2", output_parser=identity_parser)
        results = await asyncio.gather(
            executor.run_async(cmd1),
            executor.run_async(cmd2)
        )
        ```
    """

    def __init__(
        self, cwd: os.PathLike | None = None, env: dict[str, str] | None = None, timeout: float | None = None
    ) -> None:
        """Initialize the asynchronous local executor.

        Args:
            cwd: Working directory for command execution
            env: Environment variables for the subprocess

        """
        self.cwd = cwd or os.getcwd()
        self.env = env
        self.timeout = timeout

    async def run_async(self, command: Command) -> CommandResult:
        """Execute the command asynchronously and return the result.

        Args:
            command: The command to execute

        Example:
            ```python
            executor = AsyncLocalExecutor()
            cmd = Command(cmd="echo hello", output_parser=identity_parser)
            result = await executor.run_async(cmd)
            ```
        """
        proc = await asyncio.create_subprocess_shell(
            command.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            assert self.timeout is not None
            raise subprocess.TimeoutExpired(command.cmd, self.timeout)

        if proc.returncode is None:
            raise RuntimeError("Process did not complete successfully and returned no return code.")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                returncode=proc.returncode,
                cmd=command.cmd,
                output=stdout,
                stderr=stderr,
            )
        return CommandResult(
            stdout=stdout.decode(),
            stderr=stderr.decode(),
            exit_code=proc.returncode,
        )


class _DefaultExecutorMixin:
    """
    Mixin providing default executor implementations for ExecutableApp classes.

    Provides convenience methods for running commands with local executors,
    eliminating the need for applications to manually instantiate executors.
    Supports both synchronous and asynchronous execution patterns.

    Example:
        ```python
        class MyApp(ExecutableApp, _DefaultExecutorMixin):
            @property
            def command(self) -> Command:
                return Command(cmd="echo hello", output_parser=identity_parser)

        app = MyApp()

        # Run synchronously with default executor
        result = app.run()

        # Run asynchronously
        result = await app.run_async()

        # Run with custom executor kwargs
        result = app.run(executor_kwargs={"cwd": "/custom/path"})
        ```
    """

    def run(self: ExecutableApp, executor_kwargs: Optional[dict[str, Any]] = None) -> CommandResult:
        """Execute the command using a local executor and return the result."""
        executor = LocalExecutor(**(executor_kwargs or {}))
        return self.command.execute(executor)

    async def run_async(self: ExecutableApp, executor_kwargs: Optional[dict[str, Any]] = None) -> CommandResult:
        """Execute the command asynchronously using a local executor and return the result."""
        executor = AsyncLocalExecutor(**(executor_kwargs or {}))
        return await self.command.execute_async(executor)
