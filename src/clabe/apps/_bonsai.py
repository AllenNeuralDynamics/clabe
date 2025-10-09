import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, Optional, Self

import pydantic
from aind_behavior_services.utils import run_bonsai_process
from typing_extensions import override

from ..services import ServiceSettings
from ..ui import DefaultUIHelper, UiHelper
from ._base import App

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..launcher import Launcher
else:
    Launcher = Any


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
        """Resolves the path to an absolute path."""
        return Path(value).resolve()

    @pydantic.model_validator(mode="after")
    def _set_start_flag(self) -> Self:
        """Ensures that the start flag is set correctly based on the editor mode"""
        self.is_start_flag = self.is_start_flag if not self.is_editor_mode else True
        return self


class BonsaiApp(App):
    """
    A class to manage the execution of Bonsai workflows.

    Attributes:
        settings (BonsaiAppSettings): Settings for the Bonsai App
        ui_helper (UiHelper): Helper for user interface interactions.
        _result (Optional[subprocess.CompletedProcess]): Result of the Bonsai process execution.
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
            settings (BonsaiAppSettings): Settings for the Bonsai App.
            ui_helper (Optional[UiHelper]): UI helper instance. Defaults to DefaultUIHelper.
            **kwargs: Additional keyword arguments.

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
        self._result: Optional[subprocess.CompletedProcess] = None
        self.ui_helper = ui_helper if ui_helper is not None else DefaultUIHelper()

    @property
    def result(self) -> subprocess.CompletedProcess:
        """
        Returns the result of the Bonsai process execution.

        Returns:
            subprocess.CompletedProcess: The result of the Bonsai process.

        Raises:
            RuntimeError: If the app has not been run yet.
        """
        if self._result is None:
            raise RuntimeError("The app has not been run yet.")
        return self._result

    @override
    def add_app_settings(self, **kwargs):
        """
        Adds application-specific settings to the additional properties.

        Args:
            **kwargs: Additional keyword arguments.

        Returns:
            Self: The updated instance of BonsaiApp.
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
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            bool: True if validation is successful.

        Raises:
            FileNotFoundError: If any required file or directory is missing.
        """
        if not Path(self.settings.executable).exists():
            raise FileNotFoundError(f"Executable not found: {self.settings.executable}")
        if not Path(self.settings.workflow).exists():
            raise FileNotFoundError(f"Workflow file not found: {self.settings.workflow}")
        return True

    @override
    def run(self) -> subprocess.CompletedProcess:
        """
        Runs the Bonsai process.

        Returns:
            subprocess.CompletedProcess: The result of the Bonsai process execution.

        Raises:
            FileNotFoundError: If validation fails.
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
        self._result = proc
        logger.info("Bonsai process completed.")
        return proc

    @override
    def output_from_result(self, *, allow_stderr: Optional[bool]) -> Self:
        """
        Processes the output from the Bonsai process result.

        Args:
            allow_stderr (Optional[bool]): Whether to allow stderr output.

        Returns:
            Self: The updated instance of BonsaiApp.

        Raises:
            subprocess.CalledProcessError: If the process exits with an error.
        """
        proc = self.result
        try:
            proc.check_returncode()
        except subprocess.CalledProcessError:
            self._log_process_std_output("Bonsai", proc)
            raise
        else:
            self._log_process_std_output("Bonsai", proc)

            if len(proc.stdout) > 0:
                logger.error("Bonsai process finished with errors.")
                if allow_stderr is None:
                    allow_stderr = self.ui_helper.prompt_yes_no_question("Would you like to see the error message?")
                if allow_stderr is False:
                    raise subprocess.CalledProcessError(1, proc.args)
        return self

    def _log_process_std_output(self, process_name: str, proc: subprocess.CompletedProcess) -> None:
        """
        Logs the standard output and error of a process.

        Args:
            process_name (str): Name of the process.
            proc (subprocess.CompletedProcess): The process result.
        """
        if len(proc.stdout) > 0:
            logger.info("%s full stdout dump: \n%s", process_name, proc.stdout)
        if len(proc.stderr) > 0:
            logger.error("%s full stderr dump: \n%s", process_name, proc.stderr)

    def build_runner(self, allow_std_error: bool = False) -> Callable[[Launcher], Self]:
        """
        Builds a runner function for the application.

        This method returns a callable that can be executed by the launcher to run the application.

        Args:
            allow_std_error (bool): Whether to allow stderr in the output. Defaults to False.

        Returns:
            Callable[[Launcher], Self]: A callable that takes a launcher instance and returns the application instance.
        """

        def _run(launcher: Launcher):
            """Internal wrapper function"""
            try:
                self.add_app_settings(launcher=launcher)
                self.run()
                result = self.output_from_result(allow_stderr=allow_std_error)
            except subprocess.CalledProcessError as e:
                logger.error(f"App {self.__class__.__name__} failed with error: {e}")
                raise
            return result

        return _run


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
        app.add_app_settings(launcher=my_launcher)
        app.run()
        ```
    """

    def add_app_settings(self, *, launcher: Optional[Launcher] = None, **kwargs) -> Self:
        """
        Adds AIND behavior-specific application settings to the Bonsai workflow.

        This method automatically configures the TaskLogicPath, SessionPath, and RigPath
        properties for the Bonsai workflow based on the launcher's schema models.

        Args:
            launcher: The behavior launcher instance containing schema models
            **kwargs: Additional keyword arguments

        Returns:
            Self: The updated instance

        Raises:
            ValueError: If the required launcher argument is not provided

        Example:
            ```python
            # Add AIND behavior settings
            app.add_app_settings(launcher=my_launcher)
            ```
        """

        if launcher is None:
            raise ValueError("Missing required argument 'launcher'.")

        settings = {
            "TaskLogicPath": os.path.abspath(
                launcher.save_temp_model(model=launcher.get_task_logic(strict=True), directory=launcher.temp_dir)
            ),
            "SessionPath": os.path.abspath(
                launcher.save_temp_model(model=launcher.get_session(strict=True), directory=launcher.temp_dir)
            ),
            "RigPath": os.path.abspath(
                launcher.save_temp_model(model=launcher.get_rig(strict=True), directory=launcher.temp_dir)
            ),
        }
        return super().add_app_settings(**settings)
