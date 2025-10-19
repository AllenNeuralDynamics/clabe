import logging
import os
import typing as t
from pathlib import Path

import aind_behavior_curriculum.trainer
import pydantic

from ..services import ServiceSettings
from ._base import App
from ._python_script import PythonScriptApp

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


class CurriculumApp(App[CurriculumSuggestion]):
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

    def run(self) -> t.Self:
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
        self._python_script_app.run()
        return self

    def result(self, *, allow_stderr: bool = True) -> CurriculumSuggestion:
        return self._process_process_output(allow_stderr=allow_stderr)

    def _process_process_output(self, *, allow_stderr: bool | None = None) -> CurriculumSuggestion:
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
                app._process_process_output(allow_stderr=True)
                print("Curriculum completed successfully")
            except subprocess.CalledProcessError as e:
                print(f"Curriculum failed: {e}")
            ```
        """
        out = self._python_script_app._process_process_output(allow_stderr=allow_stderr)
        return CurriculumSuggestion.model_validate_json(out.stdout)

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
