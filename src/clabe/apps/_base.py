import abc
import logging
import subprocess
from typing import TYPE_CHECKING, Any, Callable, Optional, Self, TypeVar

from ..services import IService

if TYPE_CHECKING:
    from ..launcher import BaseLauncher
else:
    BaseLauncher = Any
logger = logging.getLogger(__name__)


TLauncher = TypeVar("TLauncher", bound=BaseLauncher)
TApp = TypeVar("TApp", bound="App")


class App(IService, abc.ABC):
    """
    Abstract base class representing an application that can be run and managed.

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
        add_app_settings(*args, **kwargs) -> Self:
            Adds or updates application settings. Can be overridden by subclasses
            to provide specific behavior for managing application settings.

    Notes:
        Subclasses must implement the abstract methods and property to define the specific
        behavior of the application.

    Example:
        ```python
        # Implement a custom app
        class MyApp(App):
            def run(self): return subprocess.run(["echo", "hello"])
            def output_from_result(self, allow_stderr): return self
            @property
            def result(self): return self._result

        app = MyApp()
        app.run()
        ```
    """

    @abc.abstractmethod
    def run(self) -> subprocess.CompletedProcess:
        """
        Executes the application.

        Returns:
            subprocess.CompletedProcess: The result of the application's execution.
        """
        ...

    @abc.abstractmethod
    def output_from_result(self, allow_stderr: Optional[bool]) -> Self:
        """
        Processes and returns the output from the application's result.

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

        Returns:
            subprocess.CompletedProcess: The result of the application's execution.
        """

    def add_app_settings(self, **kwargs) -> Self:
        """
        Adds or updates application settings.

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


def make_run_app_hook(app: TApp, allow_std_error: bool = False) -> Callable[[TLauncher], TApp]:
    def _run(launcher: TLauncher):
        app.add_app_settings(launcher=launcher)
        try:
            app.run()
            result = app.output_from_result(allow_stderr=allow_std_error)
        except subprocess.CalledProcessError as e:
            logger.critical(f"App {app.__class__.__name__} failed with error: {e}")
            raise
        return result

    return _run
