import abc
import logging
from typing import Any, Generic, Optional, Self, TypeVar, Protocol, runtime_checkable
from pydantic import BaseModel
from ..services import Service

logger = logging.getLogger(__name__)


class ProcessResult(BaseModel):
    """Represents the result of a process execution."""
    stdout: Optional[str]
    stderr: Optional[str]
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@runtime_checkable
class Executor(Protocol):
    def run(self, cmd: "Command") -> ProcessResult: ...


@runtime_checkable
class AsyncExecutor(Protocol):
    async def run_async(self, cmd: "Command") -> ProcessResult: ...


TOutput = TypeVar("TOutput")

class Command(Generic[TOutput], abc.ABC):

    _cmd: str
    
    def append_arg(self, arg: str) -> Self:
        """Append an argument to the command."""
        self._cmd += f" {arg}"
        return self

    def execute(self, executor: Executor) -> TOutput:
        """Execute using a synchronous executor."""
        result = executor.run(self)
        return self.parse_output(result)

    async def execute_async(self, executor: AsyncExecutor) -> TOutput:
        """Execute using an async executor."""
        result = await executor.run_async(self)
        return self.parse_output(result)

    @abc.abstractmethod
    def parse_output(self, result: ProcessResult) -> TOutput:
        """Parse the output of the command."""
        ...

TApp = TypeVar("TApp", bound="App")
TResult = TypeVar("TResult", bound=Any)


class App(Service, abc.ABC, Generic[TResult]):
    """
    Abstract base class representing an application that can be run and managed.

    Defines the interface for applications that can be executed. Subclasses must
    implement the abstract methods to define specific application behavior.

    Type Parameters:
        TResult: The type of result returned by the application

    Methods:
        run: Executes the application
        get_result: Retrieves the result of the application's execution
        add_app_settings: Adds or updates application settings
    """

    @abc.abstractmethod
    def run(self) -> Self:
        """
        Executes the application.

        This method should contain the logic to run the application and return the result of the execution.

        Returns:
            subprocess.CompletedProcess: The result of the application's execution.
        """
        ...

    @abc.abstractmethod
    def get_result(self, *, allow_stderr: bool = True) -> TResult:
        """
        Retrieves the result of the application's execution.

        This property should return the result of the application's execution.

        Returns:
            subprocess.CompletedProcess: The result of the application's execution.

        Raises:
            RuntimeError: If the application has not been run yet.
        """

    def add_app_settings(self, **kwargs) -> Self:
        """
        Adds or updates application settings.

        This method can be overridden by subclasses to provide specific behavior for managing application settings.

        Args:
            **kwargs: Keyword arguments for application settings.

        Returns:
            Self: The updated application instance.

        Example:
            ```python
            # Add application settings
            app.add_app_settings(debug=True, verbose=False)
            ```
        """
        return self
