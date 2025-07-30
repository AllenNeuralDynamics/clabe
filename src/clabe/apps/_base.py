import abc
import logging
import subprocess
from typing import TYPE_CHECKING, Any, Callable, Optional, Self, TypeVar

from ..services import Service

if TYPE_CHECKING:
    from ..launcher import BaseLauncher
else:
    BaseLauncher = Any
logger = logging.getLogger(__name__)


TApp = TypeVar("TApp", bound="App")


class App(Service, abc.ABC):
    """
    Abstract base class representing an application that can be run and managed.

    This class defines the interface for applications that can be executed and managed by the launcher.
    Subclasses must implement the abstract methods to define the specific behavior of the application.

    Attributes:
        None

    Methods:
        run() -> subprocess.CompletedProcess:
            Executes the application. Must be implemented by subclasses.
        output_from_result(allow_stderr: Optional[bool]) -> Self:
            Processes and returns the output from the application's result.
            Must be implemented by subclasses.
        result() -> subprocess.CompletedProcess:
            Retrieves the result of the application's execution.
            Must be implemented by subclasses.
        add_app_settings(**kwargs) -> Self:
            Adds or updates application settings. Can be overridden by subclasses
            to provide specific behavior for managing application settings.

    Notes:
        Subclasses must implement the abstract methods and property to define the specific
        behavior of the application.

    Example:
        ```python
        # Implement a custom app
        class MyApp(App):
            def run(self) -> subprocess.CompletedProcess: return subprocess.run(["echo", "hello"])
            def output_from_result(self, allow_stderr: Optional[bool]) -> Self: return self
            @property
            def result(self) -> subprocess.CompletedProcess: return self._result

        app = MyApp()
        app.run()
        ```
    """

    @abc.abstractmethod
    def run(self) -> subprocess.CompletedProcess:
        """
        Executes the application.

        This method should contain the logic to run the application and return the result of the execution.

        Returns:
            subprocess.CompletedProcess: The result of the application's execution.
        """
        ...

    @abc.abstractmethod
    def output_from_result(self, allow_stderr: Optional[bool]) -> Self:
        """
        Processes and returns the output from the application's result.

        This method should process the result of the application's execution and return the output.

        Args:
            allow_stderr (Optional[bool]): Whether to allow stderr in the output.

        Returns:
            Self: The processed output.
        """
        ...

    @property
    @abc.abstractmethod
    def result(self) -> subprocess.CompletedProcess:
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

    def build_runner(self, allow_std_error: bool = False) -> Callable[[BaseLauncher], Self]:
        """
        Builds a runner function for the application.

        This method returns a callable that can be executed by the launcher to run the application.

        Args:
            allow_std_error (bool): Whether to allow stderr in the output. Defaults to False.

        Returns:
            Callable[[BaseLauncher], Self]: A callable that takes a launcher instance and returns the application instance.
        """

        def _run(launcher: BaseLauncher):
            """Internal wrapper function"""
            self.add_app_settings(launcher=launcher)
            try:
                self.run()
                result = self.output_from_result(allow_stderr=allow_std_error)
            except subprocess.CalledProcessError as e:
                logger.critical(f"App {self.__class__.__name__} failed with error: {e}")
                raise
            return result

        return _run
