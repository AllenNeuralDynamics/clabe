import abc
import logging
import subprocess
from typing import Optional, Self

from ..services import IService

logger = logging.getLogger(__name__)


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
