import logging
import os
import typing as t
from pathlib import Path

import aind_behavior_curriculum.trainer
import pydantic

from ..services import ServiceSettings
from ._base import Command, ExecutableApp
from ._executors import _DefaultExecutorMixin
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

    Configuration for curriculum execution including script path, project directory,
    trainer state input, data handling, and curriculum selection.

    Attributes:
        script: The curriculum script/command to execute. Defaults to "curriculum run"
        project_directory: Root directory of the curriculum project. Defaults to current directory
        input_trainer_state: Path to the input trainer state file
        data_directory: Directory containing session data for curriculum processing
        curriculum: Optional specific curriculum name to use

    Example:
        ```python
        # Basic settings
        settings = CurriculumSettings(
            input_trainer_state="/path/to/trainer_state.json",
            data_directory="/data/session"
        )
        
        # Settings with custom curriculum
        settings = CurriculumSettings(
            script="curriculum run",
            project_directory="/path/to/curricula",
            input_trainer_state="/path/to/trainer_state.json",
            data_directory="/data/session",
            curriculum="advanced_training"
        )
        ```
    """

    __yml_section__: t.ClassVar[t.Optional[str]] = "curriculum"

    script: str = "curriculum run"
    project_directory: os.PathLike = Path(".")
    input_trainer_state: t.Optional[os.PathLike] = None
    data_directory: t.Optional[os.PathLike] = None
    curriculum: t.Optional[str] = None


class CurriculumApp(ExecutableApp, _DefaultExecutorMixin):
    """
    A curriculum application that manages the execution of behavior curriculum scripts.

    Facilitates running curriculum modules within a managed Python environment, handling
    trainer state input/output and data directory management. The app processes session
    data through curriculum logic and generates suggestions for subsequent training stages.

    Attributes:
        command: The underlying command that will be executed

    Example:
        ```python
        # Create and run curriculum app
        settings = CurriculumSettings(
            input_trainer_state="/path/to/trainer_state.json",
            data_directory="/data/session_123"
        )
        app = CurriculumApp(settings)
        app.run()
        suggestion = app.process_suggestion()
        
        # Access the updated trainer state
        new_state = suggestion.trainer_state
        metrics = suggestion.metrics
        ```
    """

    def __init__(
        self, settings: CurriculumSettings, *, python_script_app_kwargs: dict[str, t.Any] | None = None
    ) -> None:
        """
        Initializes the CurriculumApp with the specified settings.

        Configures the curriculum application by setting up the Python script runner
        with appropriate arguments for data directory, trainer state input, and
        optional curriculum selection.

        Args:
            settings: Configuration settings for the curriculum application
            python_script_app_kwargs: Optional keyword arguments to pass to PythonScriptApp

        Raises:
            ValueError: If input_trainer_state or data_directory is not set in settings

        Example:
            ```python
            # Basic initialization
            settings = CurriculumSettings(
                input_trainer_state="/path/to/state.json",
                data_directory="/data/session"
            )
            app = CurriculumApp(settings)
            
            # With custom Python script app kwargs
            app = CurriculumApp(
                settings,
                python_script_app_kwargs={"skip_validation": True}
            )
            ```
        """
        self._settings = settings

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

        python_script_app_kwargs = python_script_app_kwargs or {}
        self._python_script_app = PythonScriptApp(
            script=settings.script,
            project_directory=settings.project_directory,
            extra_uv_arguments="-q",
            additional_arguments=" ".join(f"--{key} {value}" for key, value in kwargs.items()),
            **python_script_app_kwargs,
        )

    def process_suggestion(self) -> CurriculumSuggestion:
        """
        Process and parse the curriculum command output into a CurriculumSuggestion.

        Extracts the trainer state, metrics, and version information from the
        command execution stdout.

        Returns:
            CurriculumSuggestion: Parsed curriculum suggestion with trainer state and metrics

        Raises:
            ValueError: If no stdout is available from command execution or if parsing fails

        Example:
            ```python
            app = CurriculumApp(settings)
            app.run()
            suggestion = app.process_suggestion()
            print(suggestion.trainer_state.stage.name)
            ```
        """
        if self._python_script_app.command.result.stdout is None:
            raise ValueError("No stdout from curriculum command execution.")
        return CurriculumSuggestion.model_validate_json(self._python_script_app.command.result.stdout)

    @property
    def command(self) -> Command:
        """Get the command to execute."""
        return self._python_script_app.command
