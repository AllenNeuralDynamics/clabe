import logging
from typing import Callable, Generic, Optional, Protocol, Self, TypeAlias, TypeVar, runtime_checkable

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CommandError(Exception):
    """
    Exception raised when a command execution fails.

    This exception is raised by CommandResult.check_returncode() when a command
    exits with a non-zero exit code. It includes detailed information about the
    command execution failure.

    Attributes:
        exit_code: The non-zero exit code returned by the command
        stdout: Standard output from the command (may be None)
        stderr: Standard error from the command (may be None)
        message: Human-readable error message

    Example:
        ```python
        try:
            result = CommandResult(stdout="", stderr="File not found", exit_code=1)
            result.check_returncode()
        except CommandError as e:
            print(f"Command failed with exit code {e.exit_code}")
            print(f"Error output: {e.stderr}")
        ```
    """

    def __init__(self, exit_code: int, stdout: Optional[str] = None, stderr: Optional[str] = None):
        """
        Initialize the CommandError.

        Args:
            exit_code: The non-zero exit code from the command
            stdout: Standard output from the command
            stderr: Standard error from the command
        """
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

        # Build detailed error message
        message_parts = [f"Command failed with exit code {exit_code}"]

        if stderr:
            message_parts.append(f"stderr: {stderr}")
        if stdout:
            message_parts.append(f"stdout: {stdout}")

        self.message = "\n".join(message_parts)
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return the error message."""
        return self.message

    def __repr__(self) -> str:
        """Return a detailed representation of the error."""
        return f"CommandError(exit_code={self.exit_code}, stderr={self.stderr!r}, stdout={self.stdout!r})"


class CommandResult(BaseModel):
    """Represents the result of a process execution."""

    stdout: Optional[str]
    stderr: Optional[str]
    exit_code: int

    @property
    def ok(self) -> bool:
        """Check if the command executed successfully by examining the exit code."""
        return self.exit_code == 0

    def check_returncode(self) -> None:
        """
        Raise CommandError if the exit code indicates failure.

        Raises:
            CommandError: If exit_code is non-zero, includes exit_code, stdout, and stderr

        Example:
            ```python
            result = CommandResult(stdout="output", stderr="error", exit_code=1)
            try:
                result.check_returncode()
            except CommandError as e:
                print(f"Exit code: {e.exit_code}")
                print(f"Stderr: {e.stderr}")
            ```
        """
        if not self.ok:
            raise CommandError(exit_code=self.exit_code, stdout=self.stdout, stderr=self.stderr)


@runtime_checkable
class ExecutableApp(Protocol):
    """
    Protocol defining the interface for executable applications.

    Any class implementing this protocol must provide a `command` property that
    returns a Command object, enabling standardized execution across different
    application types.

    Example:
        ```python
        class MyApp(ExecutableApp):
            @property
            def command(self) -> Command:
                return Command(cmd="echo hello", output_parser=identity_parser)
        ```
    """

    @property
    def command(self) -> "Command":
        """Get the command to execute."""
        ...


@runtime_checkable
class Executor(Protocol):
    """
    Protocol for synchronous command execution.

    Defines the interface for executing commands synchronously and obtaining
    results. Implementations should handle process execution, output capture,
    and error handling.

    Example:
        ```python
        class CustomExecutor(Executor):
            def run(self, command: Command) -> CommandResult:
                # Custom execution logic
                return CommandResult(stdout="output", stderr="", exit_code=0)
        ```
    """

    def run(self, command: "Command") -> CommandResult:
        """Execute the command and return the result."""
        ...


@runtime_checkable
class AsyncExecutor(Protocol):
    """
    Protocol for asynchronous command execution.

    Defines the interface for executing commands asynchronously using async/await
    patterns. Implementations should handle asynchronous process execution, output
    capture, and error handling.

    Example:
        ```python
        class CustomAsyncExecutor(AsyncExecutor):
            async def run_async(self, command: Command) -> CommandResult:
                # Custom async execution logic
                await asyncio.sleep(1)
                return CommandResult(stdout="output", stderr="", exit_code=0)
        ```
    """

    async def run_async(self, command: "Command") -> CommandResult:
        """Execute the command asynchronously and return the result."""
        ...


TOutput = TypeVar("TOutput")

OutputParser: TypeAlias = Callable[[CommandResult], TOutput]


class Command(Generic[TOutput]):
    """
    Represents a command to be executed with customizable output parsing.

    Encapsulates command execution logic, result management, and output parsing.
    Supports both synchronous and asynchronous execution patterns with type-safe
    output parsing.

    Attributes:
        cmd: The command string to execute
        result: The result of command execution (available after execution)

    Example:
        ```python
        # Create a simple command
        cmd = Command(cmd="echo hello", output_parser=identity_parser)

        # Execute with a synchronous executor
        executor = LocalExecutor()
        result = cmd.execute(executor)

        # Create a command with custom output parser
        def parse_json(result: CommandResult) -> dict:
            return json.loads(result.stdout)

        cmd = Command(cmd="get-data --json", output_parser=parse_json)
        data = cmd.execute(executor)
        ```
    """

    def __init__(self, cmd: str, output_parser: OutputParser[TOutput]) -> None:
        """Initialize the Command instance.
        Args:
            cmd: The command string to execute
            output_parser: Function to parse the command result into desired output type

        Example:
            ```python
            # Create a simple command
            cmd = Command(cmd="echo hello", output_parser=identity_parser)
            ```
        """
        self._cmd = cmd
        self._output_parser = output_parser
        self._result: Optional[CommandResult] = None

    @property
    def result(self) -> CommandResult:
        """Get the command result."""
        if self._result is None:
            raise RuntimeError("Command has not been executed yet.")
        return self._result

    @property
    def cmd(self) -> str:
        """Get the command string."""
        return self._cmd

    def append_arg(self, args: str | list[str]) -> Self:
        """Append an argument to the command."""
        if isinstance(args, str):
            args = [args]
        args = [arg for arg in args if arg]
        self._cmd = (self.cmd + f" {' '.join(args)}").strip()
        return self

    def execute(self, executor: Executor) -> TOutput:
        """Execute using a synchronous executor."""
        logger.info("Executing command: %s", self._cmd)
        self._set_result(executor.run(self))
        logger.info("Command execution completed.")
        return self._parse_output(self.result)

    async def execute_async(self, executor: AsyncExecutor) -> TOutput:
        """Execute using an async executor."""
        logger.info("Executing command asynchronously: %s", self._cmd)
        self._set_result(await executor.run_async(self))
        logger.info("Command execution completed.")
        return self._parse_output(self.result)

    def _set_result(self, result: CommandResult, override: bool = True) -> None:
        """Set the command result (for testing purposes)."""
        if self._result is not None and not override:
            raise RuntimeError("Result has already been set.")
        if self._result is not None and override:
            logger.warning("Overriding existing command result.")
        self._result = result

    def _parse_output(self, result: CommandResult) -> TOutput:
        """Parse the output of the command."""
        return self._output_parser(result)


def identity_parser(result: CommandResult) -> CommandResult:
    """Helper parser that returns the CommandResult as-is."""
    return result
