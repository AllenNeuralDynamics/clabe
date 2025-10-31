import logging
from typing import Callable, Generic, Optional, Protocol, Self, TypeAlias, TypeVar, runtime_checkable

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class CommandResult(BaseModel):
    """Represents the result of a process execution."""

    stdout: Optional[str]
    stderr: Optional[str]
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


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

    def run(self, command: "Command") -> CommandResult: ...


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

    async def run_async(self, command: "Command") -> CommandResult: ...


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
    return result
