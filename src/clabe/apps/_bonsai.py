import logging
import os
from os import PathLike
from pathlib import Path
from typing import ClassVar, Dict, Optional, Self

import pydantic
from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

from clabe.launcher._base import Launcher

from ..apps._base import Command, CommandResult, ExecutableApp, identity_parser
from ..services import ServiceSettings

logger = logging.getLogger(__name__)


class BonsaiAppSettings(ServiceSettings):
    """
    Settings for the BonsaiApp.

    Configuration for Bonsai workflow execution including paths, modes, and
    execution parameters.
    """

    __yml_section__: ClassVar[Optional[str]] = "bonsai"

    workflow: os.PathLike
    executable: os.PathLike = Path("./bonsai/bonsai.exe")
    is_editor_mode: bool = True
    is_start_flag: bool = True
    additional_properties: Dict[str, str] = pydantic.Field(default_factory=dict)
    cwd: Optional[os.PathLike] = None
    timeout: Optional[float] = None

    @pydantic.field_validator("workflow", "executable", mode="after", check_fields=True)
    @classmethod
    def _resolve_path(cls, value: os.PathLike) -> os.PathLike:
        """
        Resolves the path to an absolute path.

        Args:
            value: The path to resolve

        Returns:
            os.PathLike: The absolute path
        """
        return Path(value).resolve()

    @pydantic.model_validator(mode="after")
    def _set_start_flag(self) -> Self:
        """
        Ensures that the start flag is set correctly based on the editor mode.

        Returns:
            Self: The updated instance
        """
        self.is_start_flag = self.is_start_flag if not self.is_editor_mode else True
        return self


class BonsaiApp(ExecutableApp):
    """
    A class to manage the execution of Bonsai workflows.

    Handles Bonsai workflow execution, configuration management, and process
    monitoring for behavioral experiments.

    Methods:
        run: Executes the Bonsai workflow
        get_result: Retrieves the result of the Bonsai execution
        add_app_settings: Adds or updates application settings
        validate: Validates the Bonsai application configuration
    """

    def __init__(
        self, settings: BonsaiAppSettings, *, additional_externalized_properties: dict[str, str] | None = None
    ) -> None:
        """
        Initializes the BonsaiApp instance.

        Args:
            settings: Settings for the Bonsai App

        Example:
            ```python
            # Create and run a Bonsai app
            app = BonsaiApp(settings=BonsaiAppSettings(workflow="workflow.bonsai"))
            app.run()

            # Create with custom settings
            app = BonsaiApp(
                settings=BonsaiAppSettings(
                    workflow="workflow.bonsai",
                    is_editor_mode=False,
                )
            )
            app.run()
            ```
        """
        self.settings = settings
        self.validate()
        __cmd = self._build_bonsai_process_command(
            workflow_file=self.settings.workflow,
            bonsai_exe=self.settings.executable,
            is_editor_mode=self.settings.is_editor_mode,
            is_start_flag=self.settings.is_start_flag,
            additional_properties=self.settings.additional_properties | (additional_externalized_properties or {}),
        )
        self._command = Command[CommandResult](cmd=__cmd, output_parser=identity_parser)

    @property
    def command(self) -> Command[CommandResult]:
        """Get the command to execute."""
        return self._command

    def validate(self) -> None:
        """
        Returns:
            bool: True if validation is successful

        Raises:
            FileNotFoundError: If any required file or directory is missing
        """
        if not Path(self.settings.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.settings.executable}")
        if not Path(self.settings.workflow).exists():
            raise FileNotFoundError(f"Workflow file not found: {self.settings.workflow}")
        if self.settings.is_editor_mode:
            logger.warning("Bonsai will run in editor mode. Will probably not be able to assert successful completion.")

    @staticmethod
    def _build_bonsai_process_command(
        workflow_file: PathLike | str,
        bonsai_exe: PathLike | str = "bonsai/bonsai.exe",
        is_editor_mode: bool = True,
        is_start_flag: bool = True,
        additional_properties: Optional[Dict[str, str]] = None,
    ) -> str:
        """Builds a shell command that can be used to run a Bonsai workflow via subprocess"""
        output_cmd: str = f'"{bonsai_exe}" "{workflow_file}"'
        if is_editor_mode:
            if is_start_flag:
                output_cmd += " --start"
        else:
            output_cmd += " --no-editor"

        if additional_properties:
            for param, value in additional_properties.items():
                output_cmd += f' -p:"{param}"="{value}"'

        return output_cmd


class AindBehaviorServicesBonsaiApp(BonsaiApp):
    """
    Specialized Bonsai application for AIND behavior services integration.

    This class extends the base BonsaiApp to provide specific functionality for
    AIND behavior experiments, including automatic configuration of task logic,
    session, and rig paths for the Bonsai workflow.

    Example:
        ```python
        # Create an AIND behavior services Bonsai app
        app = AindBehaviorServicesBonsaiApp(workflow="behavior_workflow.bonsai")
        app.run()
        ```
    """

    def __init__(
        self,
        settings: BonsaiAppSettings,
        *,
        launcher: Launcher,
        additional_externalized_properties: dict[str, str] | None = None,
        rig: Optional[AindBehaviorRigModel] = None,
        session: Optional[AindBehaviorSessionModel] = None,
        task_logic: Optional[AindBehaviorTaskLogicModel] = None,
    ) -> None:
        """
        Adds AIND behavior services settings to the Bonsai workflow.

        Automatically configures RigPath, SessionPath, and TaskLogicPath properties
        for the Bonsai workflow based on the provided models.

        Args:
            launcher: The launcher instance for saving temporary models
            *args: Additional positional arguments
            rig: Optional rig model to configure. Defaults to None
            session: Optional session model to configure. Defaults to None
            task_logic: Optional task logic model to configure. Defaults to None
            **kwargs: Additional keyword arguments to pass to the workflow

        Returns:
            Self: The updated instance
        """
        additional_externalized_properties = additional_externalized_properties or {}
        if rig:
            additional_externalized_properties["RigPath"] = os.path.abspath(launcher.save_temp_model(model=rig))
        if session:
            additional_externalized_properties["SessionPath"] = os.path.abspath(launcher.save_temp_model(model=session))
        if task_logic:
            additional_externalized_properties["TaskLogicPath"] = os.path.abspath(
                launcher.save_temp_model(model=task_logic)
            )
        super().__init__(
            settings=settings,
            additional_externalized_properties=additional_externalized_properties,
        )
        self._launcher = launcher
