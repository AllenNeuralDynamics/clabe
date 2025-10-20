import asyncio
import logging
import os
import subprocess
from os import PathLike
from pathlib import Path
from typing import ClassVar, Dict, Optional, Self

import pydantic
from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel
from typing_extensions import override

from clabe.launcher._base import Launcher

from ..services import ServiceSettings
from ..ui import DefaultUIHelper, UiHelper
from ._base import App

logger = logging.getLogger(__name__)


class BonsaiAppSettings(ServiceSettings):
    """
    Settings for the BonsaiApp.

    Configuration for Bonsai workflow execution including paths, modes, and
    execution parameters.
    """

    __yml_section__: ClassVar[Optional[str]] = "bonsai_app"

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


class BonsaiApp(App[None]):
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
        try:
            __cmd = self._build_bonsai_process_command(
                workflow_file=self.settings.workflow,
                bonsai_exe=self.settings.executable,
                is_editor_mode=self.settings.is_editor_mode,
                is_start_flag=self.settings.is_start_flag,
                additional_properties=self.settings.additional_properties,
            )
            logger.debug("Launching Bonsai with %s", __cmd)
            cwd = self.settings.cwd or os.getcwd()

            proc = subprocess.run(__cmd, cwd=cwd, check=True, timeout=self.settings.timeout, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.error(
                "Error running Bonsai process. %s\nProcess stderr: %s", e, e.stderr if e.stderr else "No stderr output"
            )
            raise
        self._completed_process = proc
        logger.info("Bonsai process completed.")
        return self

    async def run_async(self) -> Self:
        """
        Runs the Bonsai process asynchronously without blocking.

        This method executes the Bonsai workflow in a non-blocking manner,
        allowing other async operations to run concurrently.

        Returns:
            Self: The updated instance

        Raises:
            FileNotFoundError: If validation fails
            subprocess.CalledProcessError: If the Bonsai process fails

        Example:
            ```python
            app = BonsaiApp(settings=BonsaiAppSettings(workflow="workflow.bonsai"))
            await app.run_async()
            ```
        """
        self.validate()

        if self.settings.is_editor_mode:
            logger.warning("Bonsai is running in editor mode. Cannot assert successful completion.")
        logger.info("Bonsai process running asynchronously...")

        __cmd = self._build_bonsai_process_command(
            workflow_file=self.settings.workflow,
            bonsai_exe=self.settings.executable,
            is_editor_mode=self.settings.is_editor_mode,
            is_start_flag=self.settings.is_start_flag,
            additional_properties=self.settings.additional_properties,
        )
        logger.debug("Launching Bonsai asynchronously with %s", __cmd)
        cwd = self.settings.cwd or os.getcwd()

        process = await asyncio.create_subprocess_shell(
            __cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            if self.settings.timeout is not None:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.settings.timeout,
                )
            else:
                stdout, stderr = await process.communicate()
        except asyncio.TimeoutError as err:
            process.kill()
            await process.wait()
            raise subprocess.TimeoutExpired(
                cmd=__cmd,
                timeout=self.settings.timeout or 0,
                output=None,
                stderr=None,
            ) from err

        # Unfortunately the asyncio implementation does not return a CompletedProcess
        # So, we mock one
        returncode = process.returncode if process.returncode is not None else -1
        self._completed_process = subprocess.CompletedProcess(
            args=__cmd,
            returncode=returncode,
            stdout=stdout.decode() if stdout else "",
            stderr=stderr.decode() if stderr else "",
        )

        if returncode != 0:
            logger.error(
                "Error running Bonsai process. Return code: %s\nProcess stderr: %s",
                returncode,
                self._completed_process.stderr if self._completed_process.stderr else "No stderr output",
            )
            raise subprocess.CalledProcessError(
                returncode=returncode,
                cmd=__cmd,
                output=self._completed_process.stdout,
                stderr=self._completed_process.stderr,
            )

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

    @staticmethod
    def _build_bonsai_process_command(
        workflow_file: PathLike | str,
        bonsai_exe: PathLike | str = "bonsai/bonsai.exe",
        is_editor_mode: bool = True,
        is_start_flag: bool = True,
        additional_properties: Optional[Dict[str, str]] = None,
    ) -> str:
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
