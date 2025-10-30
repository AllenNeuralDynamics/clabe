import logging
import os
from os import PathLike
from pathlib import Path
from typing import Dict, Optional

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

from clabe.launcher._base import Launcher

from ..apps._base import Command, CommandResult, ExecutableApp, identity_parser

logger = logging.getLogger(__name__)


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
        self,
        workflow: os.PathLike,
        *,
        executable: os.PathLike = Path("./bonsai/bonsai.exe"),
        is_editor_mode: bool = True,
        is_start_flag: bool = True,
        additional_properties: Optional[Dict[str, str]] = None,
        cwd: Optional[os.PathLike] = None,
        timeout: Optional[float] = None,
        additional_externalized_properties: dict[str, str] | None = None,
    ) -> None:
        """
        Initializes the BonsaiApp instance.

        Args:
            workflow: Path to the Bonsai workflow file
            executable: Path to the Bonsai executable. Defaults to "./bonsai/bonsai.exe"
            is_editor_mode: Whether to run in editor mode. Defaults to True
            is_start_flag: Whether to use the start flag. Defaults to True
            additional_properties: Additional properties to pass to Bonsai. Defaults to None
            cwd: Working directory for the process. Defaults to None
            timeout: Timeout for process execution. Defaults to None
            additional_externalized_properties: Additional externalized properties. Defaults to None

        Example:
            ```python
            # Create and run a Bonsai app
            app = BonsaiApp(workflow="workflow.bonsai")
            app.run()

            # Create with custom settings
            app = BonsaiApp(
                workflow="workflow.bonsai",
                is_editor_mode=False,
            )
            app.run()
            ```
        """
        # Resolve paths
        self.workflow = Path(workflow).resolve()
        self.executable = Path(executable).resolve()
        self.is_editor_mode = is_editor_mode
        self.is_start_flag = is_start_flag if not is_editor_mode else True
        self.additional_properties = additional_properties or {}
        self.cwd = cwd
        self.timeout = timeout

        self.validate()
        __cmd = self._build_bonsai_process_command(
            workflow_file=self.workflow,
            bonsai_exe=self.executable,
            is_editor_mode=self.is_editor_mode,
            is_start_flag=self.is_start_flag,
            additional_properties=self.additional_properties | (additional_externalized_properties or {}),
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
        if not Path(self.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.executable}")
        if not Path(self.workflow).exists():
            raise FileNotFoundError(f"Workflow file not found: {self.workflow}")
        if self.is_editor_mode:
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
        workflow: os.PathLike,
        *,
        launcher: Launcher,
        rig: Optional[AindBehaviorRigModel] = None,
        session: Optional[AindBehaviorSessionModel] = None,
        task_logic: Optional[AindBehaviorTaskLogicModel] = None,
        **kwargs,
    ) -> None:
        """
        Adds AIND behavior services settings to the Bonsai workflow.

        Automatically configures RigPath, SessionPath, and TaskLogicPath properties
        for the Bonsai workflow based on the provided models.

        Args:
            workflow: Path to the Bonsai workflow file
            launcher: The launcher instance for saving temporary models
            executable: Path to the Bonsai executable. Defaults to "./bonsai/bonsai.exe"
            is_editor_mode: Whether to run in editor mode. Defaults to True
            is_start_flag: Whether to use the start flag. Defaults to True
            additional_properties: Additional properties to pass to Bonsai. Defaults to None
            cwd: Working directory for the process. Defaults to None
            timeout: Timeout for process execution. Defaults to None
            additional_externalized_properties: Additional externalized properties. Defaults to None
            rig: Optional rig model to configure. Defaults to None
            session: Optional session model to configure. Defaults to None
            task_logic: Optional task logic model to configure. Defaults to None

        Returns:
            Self: The updated instance
        """
        additional_externalized_properties = kwargs.pop("additional_externalized_properties", {}) or {}
        if rig:
            additional_externalized_properties["RigPath"] = os.path.abspath(launcher.save_temp_model(model=rig))
        if session:
            additional_externalized_properties["SessionPath"] = os.path.abspath(launcher.save_temp_model(model=session))
        if task_logic:
            additional_externalized_properties["TaskLogicPath"] = os.path.abspath(
                launcher.save_temp_model(model=task_logic)
            )
        super().__init__(
            workflow=workflow, additional_externalized_properties=additional_externalized_properties, **kwargs
        )
        self._launcher = launcher
