import logging
import os
from os import PathLike
from pathlib import Path
from typing import Dict, Optional

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel

from clabe.launcher._base import Launcher

from ._base import Command, CommandResult, ExecutableApp, identity_parser
from ._executors import _DefaultExecutorMixin

logger = logging.getLogger(__name__)


class BonsaiApp(ExecutableApp, _DefaultExecutorMixin):
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
        Validates the Bonsai application configuration.

        Checks that the Bonsai executable and workflow file exist. Issues a warning
        if editor mode is enabled, as it may prevent proper completion detection.

        Raises:
            FileNotFoundError: If the executable or workflow file is not found

        Example:
            ```python
            app = BonsaiApp(workflow="workflow.bonsai", executable="bonsai.exe")
            app.validate()  # Called automatically during __init__
            ```
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
        """
        Builds a shell command for running a Bonsai workflow via subprocess.

        Constructs the complete command string with all necessary flags and properties
        for executing a Bonsai workflow. Handles editor mode, start flag, and
        externalized properties.

        Args:
            workflow_file: Path to the Bonsai workflow file
            bonsai_exe: Path to the Bonsai executable. Defaults to "bonsai/bonsai.exe"
            is_editor_mode: Whether to run in editor mode. Defaults to True
            is_start_flag: Whether to include the --start flag. Defaults to True
            additional_properties: Dictionary of externalized properties to pass. Defaults to None

        Returns:
            str: The complete command string

        Example:
            ```python
            cmd = BonsaiApp._build_bonsai_process_command(
                workflow_file="workflow.bonsai",
                is_editor_mode=False,
                additional_properties={"SubjectName": "Mouse123"}
            )
            # Returns: '"bonsai.exe" "workflow.bonsai" --no-editor -p:"SubjectName"="Mouse123"'
            ```
        """
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
        Initializes the AIND behavior services Bonsai app with automatic model configuration.

        Automatically configures RigPath, SessionPath, and TaskLogicPath properties
        for the Bonsai workflow by saving provided models to temporary files and
        passing their paths as externalized properties.

        Attention: This class requires a local executor since it saves temporary files.
        Args:
            workflow: Path to the Bonsai workflow file
            launcher: The launcher instance for saving temporary models
            rig: Optional rig model to configure. Defaults to None
            session: Optional session model to configure. Defaults to None
            task_logic: Optional task logic model to configure. Defaults to None
            **kwargs: Additional keyword arguments passed to BonsaiApp (executable,
                is_editor_mode, is_start_flag, additional_properties, cwd, timeout,
                additional_externalized_properties)

        Example:
            ```python
            from aind_behavior_services import (
                AindBehaviorRigModel,
                AindBehaviorSessionModel,
                AindBehaviorTaskLogicModel
            )
            
            # Create models
            rig = AindBehaviorRigModel(...)
            session = AindBehaviorSessionModel(...)
            task_logic = AindBehaviorTaskLogicModel(...)
            
            # Create app with automatic configuration
            app = AindBehaviorServicesBonsaiApp(
                workflow="behavior_workflow.bonsai",
                launcher=my_launcher,
                rig=rig,
                session=session,
                task_logic=task_logic
            )
            app.run()
            
            # The workflow will receive:
            # -p:"RigPath"="/tmp/rig_temp.json"
            # -p:"SessionPath"="/tmp/session_temp.json"
            # -p:"TaskLogicPath"="/tmp/task_logic_temp.json"
            ```
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
