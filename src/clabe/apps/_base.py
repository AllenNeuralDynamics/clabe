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
    @property
    def command(self) -> "Command":
        """Get the command to execute."""
        ...


@runtime_checkable
class Executor(Protocol):
    def run(self, cmd: "Command") -> CommandResult: ...


@runtime_checkable
class AsyncExecutor(Protocol):
    async def run_async(self, cmd: "Command") -> CommandResult: ...


TOutput = TypeVar("TOutput")

OutputParser: TypeAlias = Callable[[CommandResult], TOutput]


class Command(Generic[TOutput]):
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
        self._cmd += f" {' '.join(args)}"
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
