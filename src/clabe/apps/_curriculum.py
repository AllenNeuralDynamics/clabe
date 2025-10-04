import logging
import os
import subprocess
import typing as t
from pathlib import Path

import aind_behavior_curriculum.trainer
import pydantic

from ..launcher import Launcher
from ..services import ServiceSettings
from ._base import App
from ._python_script import PythonScriptApp

if t.TYPE_CHECKING:
    from ..launcher import Launcher
else:
    Launcher = t.Any

logger = logging.getLogger(__name__)


class CurriculumSuggestion(pydantic.BaseModel):
    """
    Model representing a curriculum suggestion with trainer state and metrics.

    This model encapsulates the output from a curriculum run, including the updated
    trainer state, performance metrics, and version information.

    Attributes:
        trainer_state: The updated trainer state after curriculum processing
        metrics: Performance metrics from the curriculum run
        version: Version of the curriculum
        dsl_version: Version of the domain-specific language package used (aind-behavior-curriculum)
    """

    trainer_state: pydantic.SerializeAsAny[aind_behavior_curriculum.trainer.TrainerState]
    metrics: pydantic.SerializeAsAny[aind_behavior_curriculum.Metrics]
    version: str
    dsl_version: str


class CurriculumSettings(ServiceSettings):
    """
    Settings for the CurriculumApp.

    Attributes:
        entry_point: Path to the curriculum module (directory or file)
        input_trainer_state: Optional path to input TrainerState serialized file
        data_directory: Optional data directory for metrics calculation
    """

    __yml_section__: t.ClassVar[t.Literal["curriculum"]] = "curriculum"

    script: str = "curriculum run"
    project_directory: os.PathLike = Path(".")
    input_trainer_state: t.Optional[os.PathLike] = None
    data_directory: t.Optional[os.PathLike] = None
    curriculum: t.Optional[str] = None


class CurriculumApp(App):
    """
    A curriculum application that manages the execution of behavior curriculum scripts.

    This class facilitates running curriculum modules within a managed Python environment,
    handling trainer state input/output and data directory management for curriculum processing.

    Attributes:
        _settings: The curriculum settings configuration
        _python_script_app: Internal Python script application for execution

    Example:
        ```python
        # Create and run a curriculum app
        settings = CurriculumSettings(entry="/path/to/curriculum")
        app = CurriculumApp(settings)
        app.run()

        # Use with launcher for automated curriculum processing
        launcher.register_callable(app.build_runner(input_state_promise))
        ```
    """

    def __init__(self, settings: CurriculumSettings):
        """
        Initializes the CurriculumApp with the specified settings.

        Args:
            settings: Configuration settings for the curriculum application

        Raises:
            FileNotFoundError: If pyproject.toml cannot be found in parent directories

        Example:
            ```python
            settings = CurriculumSettings(
                entry_point="/path/to/curriculum/module",
                data_directory="/data/session"
            )
            app = CurriculumApp(settings)
            ```
        """
        self._settings = settings

        self._python_script_app = PythonScriptApp(
            script=settings.script, project_directory=settings.project_directory, extra_uv_arguments="-q"
        )

    def run(self) -> subprocess.CompletedProcess:
        """
        Executes the curriculum module with the configured settings.

        Returns:
            subprocess.CompletedProcess: The result of the curriculum execution

        Raises:
            ValueError: If input_trainer_state or data_directory is not set
            subprocess.CalledProcessError: If the curriculum script execution fails

        Example:
            ```python
            # Set required parameters and run
            app._settings.input_trainer_state = "/path/to/trainer_state.json"
            app._settings.data_directory = "/path/to/data"
            result = app.run()
            print(f"Exit code: {result.returncode}")
            ```
        """
        if self._settings.input_trainer_state is None:
            raise ValueError("Input trainer state is not set.")
        if self._settings.data_directory is None:
            raise ValueError("Data directory is not set.")

        kwargs: dict[str, t.Any] = {  # Must use kebab casing
            "data-directory": f'"{self._settings.data_directory}"',
            "input-trainer-state": f'"{self._settings.input_trainer_state}"',
        }
        if self._settings.curriculum is not None:
            kwargs["curriculum"] = f'"{self._settings.curriculum}"'

        self._python_script_app.add_app_settings(**kwargs)
        return self._python_script_app.run()

    def output_from_result(self, *, allow_stderr: bool | None = None) -> t.Self:
        """
        Processes the output from the curriculum execution result.

        Args:
            allow_stderr: Whether to allow stderr in the output. If None, uses default behavior

        Returns:
            Self: The current CurriculumApp instance

        Raises:
            subprocess.CalledProcessError: If the process failed or stderr is present when not allowed

        Example:
            ```python
            # Process output and handle errors
            try:
                app.output_from_result(allow_stderr=True)
                print("Curriculum completed successfully")
            except subprocess.CalledProcessError as e:
                print(f"Curriculum failed: {e}")
            ```
        """
        self._python_script_app.output_from_result(allow_stderr=allow_stderr)
        return self

    def add_app_settings(self, **kwargs) -> t.Self:
        """
        Adds application-specific settings to the curriculum execution.

        Args:
            **kwargs: Additional keyword arguments to pass to the curriculum script

        Returns:
            Self: The current CurriculumApp instance

        Example:
            ```python
            # Add custom settings
            app.add_app_settings(
                debug_mode=True,
                log_level="DEBUG",
                custom_param="value"
            )
            ```
        """
        self._python_script_app.add_app_settings(**kwargs)
        return self

    @property
    def result(self) -> subprocess.CompletedProcess:
        """
        Retrieves the result of the curriculum execution.

        Returns:
            subprocess.CompletedProcess: The result of the curriculum script execution

        Raises:
            RuntimeError: If the curriculum has not been run yet

        Example:
            ```python
            # Get execution result after running
            app.run()
            result = app.result
            print(f"Return code: {result.returncode}")
            print(f"Output: {result.stdout}")
            ```
        """
        return self._python_script_app.result

    def build_runner(
        self,
        input_trainer_state: t.Callable[[], aind_behavior_curriculum.trainer.TrainerState],
        *,
        session_directory: t.Optional[t.Callable[[], os.PathLike]] = None,
        allow_std_error: bool = False,
    ) -> t.Callable[[Launcher], CurriculumSuggestion]:
        """
        Builds a runner function for curriculum execution within a launcher context.

        This method creates a callable that can be registered with a launcher to run
        the curriculum with proper data directory and trainer state management.

        Args:
            input_trainer_state: Callable containing the trainer state to process
            allow_std_error: Whether to allow stderr output without raising an error

        Returns:
            Callable that takes a Launcher and returns a CurriculumSuggestion

        Raises:
            subprocess.CalledProcessError: If curriculum execution fails

        Note:
            This method overrides the base App.build_runner to provide curriculum-specific
            functionality and return type compatibility.

        Example:
            ```python
            # Register curriculum with launcher
            trainer_state_promise = launcher.register_callable(get_trainer_state)
            curriculum_runner = CurriculumApp.build_runner(trainer_state_promise, settings)
            launcher.register_callable(curriculum_runner)
            ```
        """

        def _run(launcher: Launcher) -> CurriculumSuggestion:
            if self._settings.data_directory is None:
                self._settings.data_directory = session_directory() if session_directory else launcher.session_directory
            if self._settings.input_trainer_state is None:
                self._settings.input_trainer_state = Path(launcher.save_temp_model(input_trainer_state()))
            try:
                self.run()
                self.output_from_result(allow_stderr=allow_std_error)
            except subprocess.CalledProcessError as e:
                logger.error("App %s failed with error: %s", self.__class__.__name__, e)
                raise
            return self.get_suggestion()

        return _run

    def get_suggestion(self) -> CurriculumSuggestion:
        """
        Parses and returns the curriculum suggestion from the execution result.

        Returns:
            CurriculumSuggestion: Parsed curriculum output containing trainer state and metrics

        Raises:
            pydantic.ValidationError: If the result stdout cannot be parsed as valid JSON
            RuntimeError: If the curriculum has not been run yet

        Example:
            ```python
            # Get suggestion after running curriculum
            app.run()
            app.output_from_result()
            suggestion = app.get_suggestion()
            print(f"New trainer state: {suggestion.trainer_state}")
            print(f"Metrics: {suggestion.metrics}")
            ```
        """
        return CurriculumSuggestion.model_validate_json(self.result.stdout)
