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
    and data handling.
    """

    __yml_section__: t.ClassVar[t.Literal["curriculum"]] = "curriculum"

    script: str = "curriculum run"
    project_directory: os.PathLike = Path(".")
    input_trainer_state: t.Optional[os.PathLike] = None
    data_directory: t.Optional[os.PathLike] = None
    curriculum: t.Optional[str] = None


class CurriculumApp(ExecutableApp, _DefaultExecutorMixin):
    """
    A curriculum application that manages the execution of behavior curriculum scripts.

    Facilitates running curriculum modules within a managed Python environment, handling
    trainer state input/output and data directory management.

    Methods:
        run: Executes the curriculum script
        get_result: Retrieves the curriculum suggestion result
        add_app_settings: Adds or updates application settings
    """

    def __init__(
        self, settings: CurriculumSettings, *, python_script_app_kwargs: dict[str, t.Any] | None = None
    ) -> None:
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
        if self._python_script_app.command.result.stdout is None:
            raise ValueError("No stdout from curriculum command execution.")
        return CurriculumSuggestion.model_validate_json(self._python_script_app.command.result.stdout)

    @property
    def command(self) -> Command:
        """Get the command to execute."""
        return self._python_script_app.command
