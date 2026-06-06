import asyncio
import os
import subprocess
from typing import TYPE_CHECKING, Any, Optional

from ._base import AsyncExecutor, Command, CommandResult, Executor
from ._progress import ProgressExecutor


class LocalExecutor(Executor):
    """
    Synchronous executor for running commands on the local system.

    Executes commands using subprocess.run with configurable working directory
    and environment variables. Captures both stdout and stderr, and enforces
    return code checking.

    Commands are executed directly without shell interpretation (shell=False),
    which avoids shell injection vulnerabilities and handles arguments with
    spaces correctly.

    Attributes:
        cwd: Working directory for command execution
        env: Environment variables for the subprocess
        timeout: Maximum execution time in seconds

    Example:
        ```python
        # Create executor with default settings
        executor = LocalExecutor()

        # Create executor with custom working directory
        executor = LocalExecutor(cwd="/path/to/workdir")

        # Create executor with custom environment
        executor = LocalExecutor(env={"KEY": "value"})

        # Execute a command
        cmd = Command(cmd=["echo", "hello"], output_parser=identity_parser)
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
            timeout: Maximum execution time in seconds

        """
        self.cwd = cwd or os.getcwd()
        self.env = env
        self.timeout = timeout

    def run(self, command: Command[Any]) -> CommandResult:
        """Execute the command and return the result.

        Args:
            command: The command to execute (as a list of strings)

        Returns:
            CommandResult with stdout, stderr, and exit code

        Raises:
            CommandError: If the command exits with non-zero exit code

        Example:
            ```python
            executor = LocalExecutor()
            cmd = Command(cmd=["echo", "hello"], output_parser=identity_parser)
            result = executor.run(cmd)
            ```
        """
        proc = subprocess.run(
            command.cmd,
            cwd=self.cwd,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout,
            shell=False,
        )
        result = CommandResult(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode)
        result.check_returncode()
        return result


class AsyncLocalExecutor(AsyncExecutor):
    """
    Asynchronous executor for running commands on the local system.

    Executes commands asynchronously using asyncio subprocess functions with
    configurable working directory and environment variables. Ideal for long-running
    processes or when multiple commands need to run concurrently.

    Commands are executed directly without shell interpretation, which avoids
    shell injection vulnerabilities and handles arguments with spaces correctly.

    Attributes:
        cwd: Working directory for command execution
        env: Environment variables for the subprocess
        timeout: Maximum execution time in seconds

    Example:
        ```python
        # Create async executor
        executor = AsyncLocalExecutor()

        # Execute a command asynchronously
        cmd = Command(cmd=["echo", "hello"], output_parser=identity_parser)
        result = await executor.run_async(cmd)

        # Run multiple commands concurrently
        executor = AsyncLocalExecutor(cwd="/workdir")
        cmd1 = Command(cmd=["task1"], output_parser=identity_parser)
        cmd2 = Command(cmd=["task2"], output_parser=identity_parser)
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
            timeout: Maximum execution time in seconds

        """
        self.cwd = cwd or os.getcwd()
        self.env = env
        self.timeout = timeout

    async def run_async(self, command: Command) -> CommandResult:
        """Execute the command asynchronously and return the result.

        Args:
            command: The command to execute (as a list of strings)

        Returns:
            CommandResult with stdout, stderr, and exit code

        Raises:
            CommandError: If the command exits with non-zero exit code
            TimeoutError: If the command exceeds the timeout

        Example:
            ```python
            executor = AsyncLocalExecutor()
            cmd = Command(cmd=["echo", "hello"], output_parser=identity_parser)
            result = await executor.run_async(cmd)
            ```
        """
        proc = await asyncio.create_subprocess_exec(
            *command.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            assert self.timeout is not None
            raise subprocess.TimeoutExpired(" ".join(command.cmd), self.timeout) from exc

        if proc.returncode is None:
            raise RuntimeError("Process did not complete successfully and returned no return code.")

        command_result = CommandResult(
            stdout=stdout.decode(),
            stderr=stderr.decode(),
            exit_code=proc.returncode,
        )

        command_result.check_returncode()
        return command_result


class LocalDetachedExecutor(Executor):
    """
    Fire-and-forget executor that spawns a subprocess and returns immediately.

    Launches the command via ``subprocess.Popen`` without waiting for it to
    finish.  stdout and stderr are redirected to ``DEVNULL`` so that no pipe
    handles are left open.  The returned ``CommandResult`` is a placeholder
    with ``exit_code=0`` and no captured output; callers must not rely on it
    to reflect the actual process outcome.

    Attributes:
        cwd: Working directory for command execution
        env: Environment variables for the subprocess

    Example:
        ```python
        executor = LocalDetachedExecutor()
        cmd = Command(cmd=["bonsai", "workflow.bonsai"], output_parser=identity_parser)
        _ = executor.run(cmd)  # returns immediately; Bonsai keeps running
        ```
    """

    def __init__(self, cwd: os.PathLike | None = None, env: dict[str, str] | None = None) -> None:
        """Initialize the detached executor.

        Args:
            cwd: Working directory for command execution
            env: Environment variables for the subprocess
        """
        self.cwd = cwd or os.getcwd()
        self.env = env

    def run(self, command: Command[Any]) -> CommandResult:
        """Spawn the command and return immediately without waiting.

        Args:
            command: The command to execute (as a list of strings)

        Returns:
            A placeholder ``CommandResult`` with ``exit_code=0`` and no output.
            The actual process exit code is never captured.

        Example:
            ```python
            executor = LocalDetachedExecutor()
            cmd = Command(cmd=["bonsai", "workflow.bonsai"], output_parser=identity_parser)
            result = executor.run(cmd)  # fire-and-forget
            ```
        """
        subprocess.Popen(
            command.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        return CommandResult(stdout=None, stderr=None, exit_code=0)


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
                return Command(cmd=["echo", "hello"], output_parser=identity_parser)

        app = MyApp()

        # Run synchronously with default executor
        result = app.run()

        # Run asynchronously
        result = await app.run_async()

        # Run with custom executor kwargs
        result = app.run(executor_kwargs={"cwd": "/custom/path"})
        ```
    """

    if TYPE_CHECKING:
        # ``command`` is supplied by the ``ExecutableApp`` this mixin is combined
        # with. Declaring it here lets the type checker resolve ``self.command``
        # (and the mixin's own helpers) without typing ``self`` as the protocol.
        @property
        def command(self) -> Command:
            """The command to execute, provided by the ExecutableApp."""
            ...

    def _progress_description(self, override: Optional[str]) -> str:
        """Resolve the spinner label.

        Returns the explicit ``override`` when given, otherwise the app's class
        name, e.g. ``Running CurriculumApp``.
        """
        if override:
            return override
        return f"Running {type(self).__name__}"

    def run(
        self,
        executor_kwargs: Optional[dict[str, Any]] = None,
        *,
        show_progress: bool = True,
        progress_description: Optional[str] = None,
    ) -> CommandResult:
        """Execute the command using a local executor and return the result.

        Args:
            executor_kwargs: Keyword arguments forwarded to the local executor.
            show_progress: When True, display a live spinner with elapsed time
                while the command runs. Automatically a no-op on non-interactive
                consoles (e.g. CI or piped output). Defaults to True.
            progress_description: Label shown next to the spinner. Defaults to
                ``Running <ClassName>``.
        """
        executor: Executor = LocalExecutor(**(executor_kwargs or {}))
        if show_progress:
            executor = ProgressExecutor(executor, description=self._progress_description(progress_description))
        return self.command.execute(executor)

    async def run_async(
        self,
        executor_kwargs: Optional[dict[str, Any]] = None,
        *,
        show_progress: bool = True,
        progress_description: Optional[str] = None,
    ) -> CommandResult:
        """Execute the command asynchronously using a local executor and return the result.

        Args:
            executor_kwargs: Keyword arguments forwarded to the local executor.
            show_progress: When True, display a live spinner with elapsed time
                while the command runs. Automatically a no-op on non-interactive
                consoles (e.g. CI or piped output). Defaults to True.
            progress_description: Label shown next to the spinner. Defaults to
                ``Running <ClassName>``.
        """
        executor: AsyncExecutor = AsyncLocalExecutor(**(executor_kwargs or {}))
        if show_progress:
            executor = ProgressExecutor(executor, description=self._progress_description(progress_description))
        return await self.command.execute_async(executor)
