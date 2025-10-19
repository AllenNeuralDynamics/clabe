import logging
import os
import subprocess
from pathlib import Path
from typing import ClassVar, Dict, Optional, Self

import pydantic
from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel
from aind_behavior_services.utils import run_bonsai_process
from typing_extensions import override

from clabe.launcher._base import Launcher

from ..services import ServiceSettings
from ..ui import DefaultUIHelper, UiHelper
from ._base import App

logger = logging.getLogger(__name__)


class BonsaiAppSettings(ServiceSettings):
    """
    Settings for the BonsaiApp.

    Attributes:
        workflow (os.PathLike): Path to the Bonsai workflow file.
        executable (os.PathLike): Path to the Bonsai executable.
        is_editor_mode (bool): Whether to run Bonsai in editor mode.
        is_start_flag (bool): Whether to use the start flag when running Bonsai.
        additional_properties (Optional[Dict[str, str]]): Additional properties to pass to Bonsai.
        cwd (Optional[os.PathLike]): Working directory for the Bonsai process.
        timeout (Optional[float]): Timeout for the Bonsai process.
        print_cmd (bool): Whether to print the command being executed.
    """

    __yml_section__: ClassVar[Optional[str]] = "bonsai_app"

    workflow: os.PathLike
    executable: os.PathLike = Path("./bonsai/bonsai.exe")
    is_editor_mode: bool = True
    is_start_flag: bool = True
    additional_properties: Dict[str, str] = pydantic.Field(default_factory=dict)
    cwd: Optional[os.PathLike] = None
    timeout: Optional[float] = None
    print_cmd: bool = False

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


class BonsaiApp(App[None]):
    """
    A class to manage the execution of Bonsai workflows.

    This class handles Bonsai workflow execution, configuration management,
    and process monitoring for behavioral experiments using Bonsai.
    """

    def __init__(
        self,
        /,
        settings: BonsaiAppSettings,
        ui_helper: Optional[UiHelper] = None,
        **kwargs,
    ) -> None:
        """
        Initializes the BonsaiApp instance.

        Args:
            settings: Settings for the Bonsai App
            ui_helper: UI helper instance. Defaults to DefaultUIHelper
            **kwargs: Additional keyword arguments

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
            ```
        """
        self.settings = settings
        self._completed_process: Optional[subprocess.CompletedProcess] = None
        self.ui_helper = ui_helper if ui_helper is not None else DefaultUIHelper()

    def get_result(self, *, allow_stderr: bool = True) -> None:
        """
        Returns the result of the Bonsai process execution.

        Args:
            allow_stderr: Whether to allow stderr in the output. Defaults to True

        Returns:
            None

        Raises:
            RuntimeError: If the app has not been run yet
        """
        if self._completed_process is None:
            raise RuntimeError("The app has not been run yet.")
        return self._process_process_output(allow_stderr=allow_stderr)

    def add_app_settings(self, *args, **kwargs):
        """
        Adds application-specific settings to the additional properties.

        Args:
            *args: Positional arguments (unused)
            **kwargs: Additional keyword arguments to add to settings

        Returns:
            Self: The updated instance of BonsaiApp
        """

        if self.settings.additional_properties is not None:
            self.settings.additional_properties.update(**kwargs)
        else:
            self.settings.additional_properties = kwargs
        return self

    def validate(self, *args, **kwargs) -> bool:
        """
        Validates the existence of required files and directories.

        Args:
            *args: Additional positional arguments (unused)
            **kwargs: Additional keyword arguments (unused)

        Returns:
            bool: True if validation is successful

        Raises:
            FileNotFoundError: If any required file or directory is missing
        """
        if not Path(self.settings.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.settings.executable}")
        if not Path(self.settings.workflow).exists():
            raise FileNotFoundError(f"Workflow file not found: {self.settings.workflow}")
        return True

    @override
    def run(self) -> Self:
        """
        Runs the Bonsai process.

        Returns:
            Self: The updated instance

        Raises:
            FileNotFoundError: If validation fails
            subprocess.CalledProcessError: If the Bonsai process fails
        """
        self.validate()

        if self.settings.is_editor_mode:
            logger.warning("Bonsai is running in editor mode. Cannot assert successful completion.")
        logger.info("Bonsai process running...")
        proc = run_bonsai_process(
            workflow_file=self.settings.workflow,
            bonsai_exe=self.settings.executable,
            is_editor_mode=self.settings.is_editor_mode,
            is_start_flag=self.settings.is_start_flag,
            additional_properties=self.settings.additional_properties,
            cwd=self.settings.cwd,
            timeout=self.settings.timeout,
            print_cmd=self.settings.print_cmd,
        )
        self._completed_process = proc
        logger.info("Bonsai process completed.")
        return self

    def _process_process_output(self, *, allow_stderr: Optional[bool]) -> None:
        """
        Processes the output from the Bonsai process result.

        Args:
            allow_stderr: Whether to allow stderr output. If None, prompts user

        Returns:
            None

        Raises:
            RuntimeError: If the app has not been run yet
            subprocess.CalledProcessError: If the process exits with an error
        """
        proc = self._completed_process
        if proc is None:
            raise RuntimeError("The app has not been run yet.")

        try:
            proc.check_returncode()
        except subprocess.CalledProcessError:
            self._log_process_std_output("Bonsai", proc)
            raise
        else:
            self._log_process_std_output("Bonsai", proc)
            if len(proc.stderr) > 0:
                logger.error("Bonsai process finished with errors.")
                if allow_stderr is None:
                    allow_stderr = self.ui_helper.prompt_yes_no_question("Would you like to see the error message?")
                if allow_stderr is False:
                    raise subprocess.CalledProcessError(1, proc.args)
        return

    def _log_process_std_output(self, process_name: str, proc: subprocess.CompletedProcess) -> None:
        """
        Logs the standard output and error of a process.

        Args:
            process_name: Name of the process
            proc: The process result
        """
        if len(proc.stdout) > 0:
            logger.info("%s full stdout dump: \n%s", process_name, proc.stdout)
        if len(proc.stderr) > 0:
            logger.error("%s full stderr dump: \n%s", process_name, proc.stderr)


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

    def add_app_settings(
        self,
        launcher: Launcher,
        *args,
        rig: Optional[AindBehaviorRigModel] = None,
        session: Optional[AindBehaviorSessionModel] = None,
        task_logic: Optional[AindBehaviorTaskLogicModel] = None,
        **kwargs,
    ) -> Self:  # type: ignore[override]
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
        settings = {}
        if rig:
            settings["RigPath"] = os.path.abspath(launcher.save_temp_model(model=rig))
        if session:
            settings["SessionPath"] = os.path.abspath(launcher.save_temp_model(model=session))
        if task_logic:
            settings["TaskLogicPath"] = os.path.abspath(launcher.save_temp_model(model=task_logic))

        settings.update(kwargs)
        return super().add_app_settings(*args, **settings)
